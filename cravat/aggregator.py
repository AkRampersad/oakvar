import os
import argparse
import sys
import sqlite3
import re
import time
import logging
import oyaml as yaml
from cravat.inout import CravatReader
from cravat.inout import CravatWriter
from cravat.inout import ColumnDefinition
import cravat.admin_util as au
import json
from .exceptions import BadFormatError
import traceback
from distutils.version import LooseVersion

class Aggregator (object):

    cr_type_to_sql = {'string':'text',
                      'int':'integer',
                      'float':'real'}
    commit_threshold = 10000

    def __init__(self, cmd_args, status_writer):
        self.status_writer = status_writer
        self.annotators = []
        self.ipaths = {}
        self.readers = {}
        self.base_fpath = None
        self.level = None
        self.input_dir = None
        self.input_base_fname = None
        self.output_dir = None
        self.output_base_fname = None
        self.key_name = None
        self.table_name = None
        self.base_prefix = 'base'
        self.base_dir = os.path.abspath(__file__)
        self.parse_cmd_args(cmd_args)
        self._setup_logger()

    def parse_cmd_args(self, cmd_args):
        parser = argparse.ArgumentParser()
        parser.add_argument('path',
                            help='Path to this aggregator module')
        parser.add_argument('-i',
                            dest='input_dir',
                            required=True,
                            help='Directory containing annotator outputs')
        parser.add_argument('-l',
                            dest='level',
                            required= True,
                            help='Level to aggregate')
        parser.add_argument('-n',
                            dest='name',
                            required=True,
                            help='Name of run')
        parser.add_argument('-d',
                            dest='output_dir',
                            help='Directory for aggregator output. '\
                                 +'Default is input directory.')
        parser.add_argument('-x',
                            dest='delete',
                            action='store_true',
                            help='Deletes the existing one and creates ' +\
                                 'a new one.')
        parsed = parser.parse_args(cmd_args)
        self.level = parsed.level
        self.name = parsed.name
        self.input_dir = os.path.abspath(parsed.input_dir)
        if parsed.output_dir:
            self.output_dir = parsed.output_dir
        else:
            self.output_dir = self.input_dir
        self.set_input_base_fname()
        if self.input_base_fname == None:
            exit()
        self.set_output_base_fname()
        if not(os.path.exists(self.output_dir)):
            os.makedirs(self.output_dir)
        self.delete = parsed.delete

    def _setup_logger(self):
        self.logger = logging.getLogger('cravat.aggregator')
        self.logger.info('level: {0}'.format(self.level))
        self.logger.info('input directory: %s' %self.input_dir)
        self.error_logger = logging.getLogger('error.aggregator')
        self.unique_excs = []

    def run(self):
        self._setup()
        if self.input_base_fname == None:
            return
        start_time = time.time()
        self.status_writer.queue_status_update('status', 'Started {} ({})'.format('Aggregator', self.level))
        last_status_update_time = time.time()
        self.logger.info('started: %s' %\
                         time.asctime(time.localtime(start_time)))
        self.dbconn.commit()
        self.cursor.execute('pragma synchronous=0;')
        self.cursor.execute('pragma journal_mode=WAL;')
        n = 0
        # Prepare insert statement
        col_names = self.base_reader.get_column_names()
        q = 'insert into {table} ({columns}) values ({placeholders});'.format(
            table = self.table_name,
            columns = ', '.join(col_names),
            placeholders = ', '.join(['?']*len(col_names))
        )
        # Insert rows
        for lnum, line, rd in self.base_reader.loop_data():
            try:
                n += 1
                vals = [rd.get(c) for c in col_names]
                self.cursor.execute(q, vals)
                if n%self.commit_threshold == 0:
                    self.dbconn.commit()
                cur_time = time.time()
                if lnum % 10000 == 0 or cur_time - last_status_update_time > 3:
                    self.status_writer.queue_status_update('status', 'Running {} ({}): line {}'.format('Aggregator', self.level + ':base', lnum))
                    last_status_update_time = cur_time
            except Exception as e:
                self._log_runtime_error(lnum, line, e)
        self.dbconn.commit()
        for annot_name in self.annotators:
            reader = self.readers[annot_name]
            n = 0
            for lnum, line, rd in reader.loop_data():
                try:
                    n += 1
                    key_val = rd[self.key_name]
                    reader_col_names = [x for x in rd if x != self.key_name]
                    update_toks = []
                    for col_name in reader_col_names:
                        val = rd[col_name]
                        set_val = 'null'
                        if val is not None:
                            if type(val) is str:
                                set_val = '"%s"' %val
                            else:
                                set_val = str(val)
                        update_toks.append('%s=%s' %(col_name, set_val))
                    q = 'update %s set %s where %s="%s";' %(
                        self.table_name,
                        ', '.join(update_toks),
                        self.base_prefix + '__' + self.key_name,
                        key_val)
                    self.cursor.execute(q)
                    if n%self.commit_threshold == 0:
                        self.dbconn.commit()
                    cur_time = time.time()
                    if lnum % 10000 == 0 or cur_time - last_status_update_time > 3:
                        self.status_writer.queue_status_update('status', 'Running {} ({}): line {}'.format('Aggregator', self.level + ':base', lnum))
                        last_status_update_time = cur_time
                except Exception as e:
                    self._log_runtime_error(lnum, line, e)
            self.dbconn.commit()
        self.fill_categories()
        self.cursor.execute('pragma synchronous=2;')
        self.cursor.execute('pragma journal_mode=delete;')
        end_time = time.time()
        self.logger.info('finished: %s' %time.asctime(time.localtime(end_time)))
        runtime = end_time - start_time
        self.logger.info('runtime: %s' %round(runtime, 3))
        self._cleanup()
        self.status_writer.queue_status_update('status', 'Finished {} ({})'.format('Aggregator', self.level))

    def make_reportsub (self):
        if self.level in ['variant', 'gene']:
            q = 'select * from {}_reportsub'.format(self.level)
            self.cursor.execute(q)
            self.reportsub = {}
            for r in self.cursor.fetchall():
                (col_name, sub) = r
                self.reportsub[col_name] = json.loads(sub)
        else:
            self.reportsub = {}

    def do_reportsub_col_cats (self, col_name, col_cats):
        (module_name, col) = col_name.split('__')
        if module_name in self.reportsub and col in self.reportsub[module_name]:
            sub = self.reportsub[module_name][col]
            for k,v in sub.items():
                for i,_ in enumerate(col_cats):
                    col_cats[i] = col_cats[i].replace(k, v)
        return col_cats

    def fill_categories (self):
        header_table = self.level+'_header'
        coldefs = []
        if LooseVersion(au.get_current_package_version()) >= LooseVersion('1.5.0'):
            sql = 'select col_def from '+header_table
            self.cursor.execute(sql)
            for row in self.cursor:
                coljson = row[0]
                coldef = ColumnDefinition({})
                coldef.from_json(coljson)
                coldefs.append(coldef)
        else:
            sql = 'pragma table_info("{}")'.format(header_table)
            self.cursor.execute(sql)
            header_cols = [row[1] for row in self.cursor.fetchall()]
            select_order = [cname for cname in ColumnDefinition.db_order if cname in header_cols]
            sql = 'select {} from {}'.format(
                ', '.join(select_order),
                header_table
            )
            self.cursor.execute(sql)
            column_headers = self.cursor.fetchall()
            for column_header in column_headers:
                coldef = ColumnDefinition({})
                coldef.from_row(column_header, order=select_order)
                coldefs.append(coldef)
        for coldef in coldefs:
            col_cats = coldef.categories
            if coldef.category in ['single', 'multi']:
                if col_cats is not None and len(col_cats) == 0:
                    q = 'select distinct {} from {}'.format(coldef.name, self.level)
                    self.cursor.execute(q)
                    col_set = set([])
                    for r in self.cursor:
                        if r[0] == None:
                            continue
                        col_set.update(r[0].split(';'))
                    col_cats = list(col_set)
                    col_cats.sort()
                else:
                    col_cats = self.do_reportsub_col_cats(coldef.name, col_cats)
                coldef.categories = col_cats
                self.update_col_def(coldef)
        self.dbconn.commit()

    def update_col_def (self, col_def):
        q = 'update {}_header set col_def=? where col_name=?'.format(self.level)
        self.cursor.execute(q, [col_def.get_json(), col_def.name])

    def _cleanup(self):
        self.cursor.close()
        self.dbconn.close()

    def set_input_base_fname (self):
        crv_fname = self.name + '.crv'
        crx_fname = self.name + '.crx'
        crg_fname = self.name + '.crg'
        crs_fname = self.name + '.crs'
        crm_fname = self.name + '.crm'
        for fname in os.listdir(self.input_dir):
            if self.level == 'variant':
                if fname == crx_fname:
                    self.input_base_fname = fname
                elif fname == crv_fname and not self.input_base_fname:
                    self.input_base_fname = fname
            elif self.level == 'gene' and fname == crg_fname:
                self.input_base_fname = fname
            elif self.level == 'sample' and fname == crs_fname:
                self.input_base_fname = fname
            elif self.level == 'mapping' and fname == crm_fname:
                self.input_base_fname = fname

    def set_output_base_fname (self):
        self.output_base_fname = self.name

    def _setup(self):
        if self.level == 'variant':
            self.key_name = 'uid'
        elif self.level == 'gene':
            self.key_name = 'hugo'
        elif self.level == 'sample':
            self.key_name = ''
        elif self.level == 'mapping':
            self.key_name = ''
        self.table_name = self.level
        self.header_table_name = self.table_name + '_header'
        self.reportsub_table_name = self.table_name + '_reportsub'
        annot_name_re = re.compile('.*\.(.*)\.[var,gen]')
        for fname in os.listdir(self.input_dir):
            if fname.startswith(self.name + '.'):
                if self.level == 'variant' and fname.endswith('.var'):
                    annot_name_match = annot_name_re.match(fname)
                    annot_name = annot_name_match.group(1)
                    self.annotators.append(annot_name)
                    self.ipaths[annot_name] = \
                        os.path.join(self.input_dir, fname)
                elif self.level == 'gene' and fname.endswith('.gen'):
                    annot_name_match = annot_name_re.match(fname)
                    annot_name = annot_name_match.group(1)
                    self.annotators.append(annot_name)
                    self.ipaths[annot_name] = \
                        os.path.join(self.input_dir, fname)
        self.annotators.sort()
        self.base_fpath = os.path.join(self.input_dir, self.input_base_fname)
        self._setup_io()
        self._setup_table()

    def _setup_table(self):
        columns = []
        unique_names = set([])
        # annotator table
        annotator_table = self.level + '_annotator'
        q = 'drop table if exists {:}'.format(annotator_table)
        self.cursor.execute(q)
        q = 'create table {:} (name text, displayname text, version text)'.format(
            annotator_table)
        self.cursor.execute(q)
        q = 'insert into {:} values ("{:}", "{:}", "{:}")'.format(
            annotator_table, 'base', 'Variant Annotation', "")
        self.cursor.execute(q)
        for _, col_def in self.base_reader.get_all_col_defs().items():
            col_name = self.base_prefix + '__' + col_def.name
            col_def.name = col_name
            columns.append(col_def)
            unique_names.add(col_name)
        for annot_name in self.annotators:
            reader = self.readers[annot_name]
            annotator_name = reader.get_annotator_name()
            if annotator_name == '':
                annotator_name = annot_name
            annotator_displayname = reader.get_annotator_displayname()
            if annotator_displayname == '':
                annotator_displayname = annotator_name.upper()
            annotator_version = reader.get_annotator_version()
            q = 'insert into {:} values ("{:}", "{:}", "{:}")'.format(
                annotator_table, annotator_name, annotator_displayname, annotator_version)
            self.cursor.execute(q)
            orded_col_index = sorted(list(reader.get_all_col_defs().keys()))
            for col_index in orded_col_index:
                col_def = reader.get_col_def(col_index)
                reader_col_name = col_def.name
                if reader_col_name == self.key_name: continue
                col_def.name = '%s__%s' %(annot_name, reader_col_name)
                if col_def.name in unique_names:
                    err_msg = 'Duplicate column name %s found in %s. ' \
                        %(col_def.name, reader.path)
                    sys.exit(err_msg)
                else:
                    columns.append(col_def)
                    unique_names.add(col_def.name)
        col_def_strings = []
        for col_def in columns:
            name = col_def.name
            sql_type = self.cr_type_to_sql[col_def.type]
            s = name + ' ' + sql_type
            col_def_strings.append(s)
        # data table
        q = 'drop table if exists %s' %self.table_name
        self.cursor.execute(q)
        q = 'create table %s (%s);' \
            %(self.table_name, ', '.join(col_def_strings))
        self.cursor.execute(q)
        # index tables
        index_n = 0
        # index_columns is a list of columns to include in this index
        for index_columns in self.base_reader.get_index_columns():
            cols = ['base__{0}'.format(x) for x in index_columns]
            q = 'create index {table_name}_idx_{idx_num} on {table_name} ({columns});'\
                .format(table_name = self.table_name,
                        idx_num = str(index_n),
                        columns = ', '.join(cols)
                        )
            self.cursor.execute(q)
            index_n += 1
        # header table
        q = 'drop table if exists %s' %self.header_table_name
        self.cursor.execute(q)
        q = 'create table %s (col_name text, col_def text);' \
            %(self.header_table_name)
        self.cursor.execute(q)
        for col_def in columns:
            col_row = [col_def.name, col_def.get_json()]
            # use prepared statement to allow " characters in categories and desc
            insert_template = 'insert into {} values (?, ?)'.format(self.header_table_name)
            self.cursor.execute(insert_template, col_row)
        # report substitution table
        if self.level in ['variant', 'gene']:
            q = 'drop table if exists {}'.format(self.reportsub_table_name)
            self.cursor.execute(q)
            q = 'create table {} (module text, subdict text)'.format(self.reportsub_table_name)
            self.cursor.execute(q)
            if hasattr(self.base_reader, 'report_substitution'):
                sub = self.base_reader.report_substitution
                if sub:
                    module = 'base'
                    q = 'insert into {} values (\'{}\', \'{}\')'.format(
                        self.reportsub_table_name,
                        'base',
                        json.dumps(sub)
                    )
                    self.cursor.execute(q)
            for module in self.readers:
                if hasattr(self.base_reader, 'report_substitution'):
                    sub = self.readers[module].report_substitution
                    if sub:
                        q = 'insert into {} values ("{}", \'{}\')'.format(
                            self.reportsub_table_name,
                            module,
                            json.dumps(sub)
                        )
                        self.cursor.execute(q)
        self.make_reportsub()
        # filter and layout save table
        q = 'drop table if exists viewersetup'
        self.cursor.execute(q)
        q = 'create table viewersetup (datatype text, name text, viewersetup text, unique (datatype, name))'
        self.cursor.execute(q)
        self.dbconn.commit()

    def _setup_io(self):
        self.base_reader = CravatReader(self.base_fpath)
        for annot_name in self.annotators:
            self.readers[annot_name] = CravatReader(self.ipaths[annot_name])
        self.db_fname = self.output_base_fname + '.sqlite'
        self.db_path = os.path.join(self.output_dir, self.db_fname)
        if self.delete and os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.dbconn = sqlite3.connect(self.db_path)
        self.cursor = self.dbconn.cursor()

    def _log_runtime_error(self, ln, line, e):
        err_str = traceback.format_exc().rstrip()
        if ln is not None and line is not None:
            if err_str not in self.unique_excs:
                self.unique_excs.append(err_str)
                self.logger.error(err_str)
            self.error_logger.error('\nLINE:{:d}\nINPUT:{}\nERROR:{}\n#'.format(ln, line[:-1], str(e)))
        else:
            self.logger.error(err_str)

if __name__ == '__main__':
    aggregator = Aggregator(sys.argv)
    aggregator.run()
