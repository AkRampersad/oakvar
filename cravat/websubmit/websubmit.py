import os
import time
import datetime
import subprocess
import yaml
import json
from cravat import admin_util as au
from cravat.config_loader import ConfigLoader
from cravat.cravat_class import run_cravat_job
import sys
import traceback
import shutil
from aiohttp import web
import aiosqlite3
import hashlib
from distutils.version import LooseVersion
import glob
import platform
import signal
import multiprocessing as mp
import asyncio
import importlib
from multiprocessing import Process, Pipe, Value, Manager, Queue
from queue import Empty
from cravat import constants
from cravat import get_live_annotator, get_live_mapper
if importlib.util.find_spec('cravatserver') is not None:
    import cravatserver

cfl = ConfigLoader()

class FileRouter(object):

    def __init__(self):
        self.root = os.path.dirname(__file__)
        self.input_fname = 'input'
        self.report_extensions = {
            'text':'.tsv',
            'excel':'.xlsx',
            'vcf': '.vcf'
        }
        self.db_extension = '.sqlite'
        self.log_extension = '.log'
        self.status_extension = '.status.json'

    async def get_jobs_dirs (self, request):
        root_jobs_dir = au.get_jobs_dir()
        global servermode
        if servermode:
            username = await cravatserver.get_username(request)
        else:
            username = 'default'
        if username == 'admin':
            jobs_dirs = []
            fns = os.listdir(root_jobs_dir)
            for fn in fns:
                path = os.path.join(root_jobs_dir, fn)
                if os.path.isdir(path):
                    jobs_dirs.append(path)
        else:
            jobs_dir = os.path.join(root_jobs_dir, username)
            if os.path.exists(jobs_dir) == False:
                os.mkdir(jobs_dir)
            jobs_dirs = [jobs_dir]
        return jobs_dirs

    async def job_dir(self, request, job_id):
        jobs_dirs = await self.get_jobs_dirs(request)
        if jobs_dirs == None:
            return None
        else:
            if servermode:
                username = await cravatserver.get_username(request)
                if username != 'admin':
                    return os.path.join(jobs_dirs[0], job_id)
                else:
                    for jobs_dir in jobs_dirs:
                        job_dir = os.path.join(jobs_dir, job_id)
                        if os.path.exists(job_dir):
                            return job_dir
                    return None
            else:
                return os.path.join(jobs_dirs[0], job_id)

    async def job_input(self, request, job_id):
        job_dir, statusjson = await self.job_status(request, job_id)
        orig_input_fname = None
        if 'orig_input_fname' in statusjson:
            orig_input_fname = statusjson['orig_input_fname']
        else:
            fns = os.listdir(job_dir)
            for fn in fns:
                if fn.endswith('.crv'):
                    orig_input_fname = fn[:-4]
                    break
        if orig_input_fname is not None:
            orig_input_path = os.path.join(job_dir, orig_input_fname)
        else:
            orig_input_path = None
        return orig_input_path
    
    async def job_run_name(self, request, job_id):
        job_dir, statusjson = await self.job_status(request, job_id)
        run_name = statusjson.get('run_name')
        if run_name is None:
            fns = os.listdir(job_dir)
            for fn in fns:
                if fn.endswith('.crv'):
                    run_name = fn[:-4]
                    break
        return run_name

    async def job_run_path(self, request, job_id):
        job_dir, _ = await self.job_status(request, job_id)
        if job_dir is None:
            run_path = None
        else:
            run_name = await self.job_run_name(request, job_id)
            if run_name is not None:
                run_path = os.path.join(job_dir, run_name)
            else:
                run_path = None
        return run_path

    async def job_db(self, request, job_id):
        run_path = await self.job_run_path(request, job_id)
        output_fname = run_path + self.db_extension
        return output_fname

    async def job_status_path (self, request, job_id):
        run_path = await self.job_run_path(request, job_id)
        output_fname = run_path + self.status_extension
        return output_fname

    async def job_report(self, request, job_id, report_type):
        ext = self.report_extensions.get(report_type, '.'+report_type)
        run_path = await self.job_run_path(request, job_id)
        if run_path is None:
            return None
        report_path = run_path + ext
        return report_path

    async def job_status (self, request, job_id):
        try:
            job_dir = await self.job_dir(request, job_id)
            fns = os.listdir(job_dir)
            statusjson = {}
            for fn in fns:
                if fn.endswith('.status.json'):
                    with open(os.path.join(job_dir, fn)) as f:
                        statusjson = json.loads(f.readline())
                elif fn.endswith('.info.yaml'):
                    with open(os.path.join(job_dir, fn)) as f:
                        statusjson = yaml.load(f)
        except Exception as e:
            traceback.print_exc()
            job_dir = None
            statusjson = None
        return job_dir, statusjson

    async def job_log (self, request, job_id):
        run_path = await self.job_run_path(request, job_id)
        if run_path is not None:
            log_path = run_path + '.log'
            if os.path.exists(log_path) == False:
                log_path = None
        else:
            log_path = None
        return log_path

class WebJob(object):
    def __init__(self, job_dir, job_status_fpath):
        self.info = {}
        self.info['orig_input_fname'] = ''
        self.info['assembly'] = ''
        self.info['note'] = ''
        self.info['db_path'] = ''
        self.info['viewable'] = False
        self.info['reports'] = []
        self.info['annotators'] = ''
        self.info['annotator_version'] = ''
        self.info['open_cravat_version'] = ''
        self.info['num_input_var'] = ''
        self.info['submission_time'] = ''
        self.job_dir = job_dir
        self.job_status_fpath = job_status_fpath
        self.info['id'] = os.path.basename(job_dir)

    def save_job_options (self, job_options):
        self.set_values(**job_options)

    def read_info_file(self):
        if os.path.exists(self.job_status_fpath) == False:
            info_dict = {'status': 'Error'}
        else:
            with open(self.job_status_fpath) as f:
                info_dict = yaml.load(f)
        if info_dict != None:
            self.set_values(**info_dict)

    def set_info_values(self, **kwargs):
        self.set_values(**kwargs)

    def get_info_dict(self):
        return self.info

    def set_values(self, **kwargs):
        self.info.update(kwargs)

def get_next_job_id():
    return datetime.datetime.now().strftime(r'%y%m%d-%H%M%S')

async def submit (request):
    global filerouter
    global servermode
    if servermode:
        r = await cravatserver.is_loggedin(request)
        if r == False:
            return web.json_response({'status': 'notloggedin'})
    jobs_dirs = await filerouter.get_jobs_dirs(request)
    jobs_dir = jobs_dirs[0]
    job_id = get_next_job_id()
    job_dir = os.path.join(jobs_dir, job_id)
    os.makedirs(job_dir, exist_ok=True)
    reader = await request.multipart()
    job_options = {}
    input_files = []
    while True:
        part = await reader.next()
        if not part: 
            break 
        if part.name.startswith('file_'):
            input_files.append(part)
            # Have to write to disk here
            wfname = part.filename
            wpath = os.path.join(job_dir, wfname)
            with open(wpath,'wb') as wf:
                wf.write(await part.read())
        elif part.name == 'options':
            job_options = await part.json()
    input_fnames = [fp.filename for fp in input_files]
    run_name = input_fnames[0]
    if len(input_fnames) > 1:
        run_name += '_and_'+str(len(input_fnames)-1)+'_files'
    info_fname = '{}.status.json'.format(run_name)
    job_info_fpath = os.path.join(job_dir, info_fname)
    job = WebJob(job_dir, job_info_fpath)
    job.save_job_options(job_options)
    job.set_info_values(
                        orig_input_fname=input_fnames,
                        run_name=run_name,
                        submission_time=datetime.datetime.now().isoformat(),
                        viewable=False
                        )
    # Subprocess arguments
    input_fpaths = [os.path.join(job_dir, fn) for fn in input_fnames]
    tot_lines = 0
    for fpath in input_fpaths:
        with open(fpath) as f:
            tot_lines += count_lines(f)
    run_args = ['cravat']
    for fn in input_fnames:
        run_args.append(os.path.join(job_dir, fn))
    # Annotators
    if len(job_options['annotators']) > 0:
        annotators = job_options['annotators']
        annotators.sort()
        run_args.append('-a')
        run_args.extend(annotators)
    else:
        annotators = ''
        run_args.append('-e')
        run_args.append('*')
    # Liftover assembly
    run_args.append('-l')
    run_args.append(job_options['assembly'])
    au.set_cravat_conf_prop('last_assembly', job_options['assembly'])
    # Reports
    if len(job_options['reports']) > 0:
        run_args.append('-t')
        run_args.extend(job_options['reports'])
    else:
        run_args.extend(['--skip', 'reporter'])
    # Note
    if 'note' in job_options:
        note = job_options['note']
    else:
        note = ''
    run_args.append('--note')
    run_args.append(job_options['note'])
    # Forced input format
    if 'forcedinputformat' in job_options:
        run_args.append('--forcedinputformat')
        run_args.append(job_options['forcedinputformat'])
    if servermode:
        run_args.append('--writeadmindb')
        run_args.extend(['--jobid', job_id])
    global job_queue
    global run_jobs_info
    job_ids = run_jobs_info['job_ids']
    job_ids.append(job_id)
    run_jobs_info['job_ids'] = job_ids
    qitem = {'cmd': 'submit', 'job_id': job_id, 'run_args': run_args}
    job_queue.put(qitem)
    status = {'status': 'Submitted'}
    job.set_info_values(status=status)
    if servermode:
        await cravatserver.add_job_info(request, job)
    # makes temporary status.json
    wf = open(os.path.join(job_dir, run_name + '.status.json'), 'w')
    status_json = {}
    status_json['job_dir'] = job_dir
    status_json['id'] = job_id
    status_json['run_name'] = run_name
    status_json['assembly'] = ''
    status_json['db_path'] = ''
    status_json['orig_input_fname'] = input_fnames
    status_json['orig_input_path'] = input_fpaths
    status_json['submission_time'] = datetime.datetime.now().isoformat()
    status_json['viewable'] = False
    status_json['note'] = note
    status_json['status'] = 'Submitted'
    status_json['reports'] = []
    pkg_ver = au.get_current_package_version()
    status_json['open_cravat_version'] = pkg_ver
    status_json['annotators'] = annotators
    wf.write(json.dumps(status_json))
    wf.close()
    return web.json_response(job.get_info_dict())

def count_lines(f):
    n = 0
    for _ in f:
        n+=1
    return n

def get_expected_runtime(num_lines, annotators):
    mapper_vps = 1000
    annot_vps = 5000
    agg_vps = 8000
    return num_lines*(1/mapper_vps + len(annotators)/annot_vps + 1/agg_vps)

def get_annotators(request):
    out = {}
    for local_info in au.get_local_module_infos(types=['annotator']):
        module_name = local_info.name
        if local_info.type == 'annotator':
            out[module_name] = {
                                'name':module_name,
                                'version':local_info.version,
                                'type':local_info.type,
                                'title':local_info.title,
                                'description':local_info.description,
                                'developer': local_info.developer
                                }
    return web.json_response(out)

def find_files_by_ending (d, ending):
    fns = os.listdir(d)
    files = []
    for fn in fns:
        if fn.endswith(ending):
            files.append(fn)
    return files

async def get_job (request, job_id):
    global filerouter
    '''
    jobs_dirs = await filerouter.get_jobs_dirs(request)
    if jobs_dir is None:
        return None
    if os.path.exists(jobs_dir) == False:
        os.makedirs(jobs_dir)
    job_dir = os.path.join(jobs_dir, job_id)
    '''
    job_dir = await filerouter.job_dir(request, job_id)
    if os.path.exists(job_dir) == False:
        job = WebJob(job_dir, None)
        job.info['status'] = 'Error'
        return job
    if os.path.isdir(job_dir) == False:
        job = WebJob(job_dir, None)
        job.info['status'] = 'Error'
        return job
    fns = find_files_by_ending(job_dir, '.status.json')
    if len(fns) < 1:
        job = WebJob(job_dir, None)
        job.info['status'] = 'Error'
        return job
    status_fname = fns[0]
    status_fpath = os.path.join(job_dir, status_fname)
    job = WebJob(job_dir, status_fpath)
    job.read_info_file()
    fns = find_files_by_ending(job_dir, '.info.yaml')
    if len(fns) > 0:
        info_fpath = os.path.join(job_dir, fns[0])
        with open (info_fpath) as f:
            info_json = yaml.load('\n'.join(f.readlines()))
            for k, v in info_json.items():
                if k == 'status' and 'status' in job.info:
                    continue
                job.info[k] = v
    global run_jobs_info
    if 'status' not in job.info:
        job.info['status'] = 'Aborted'
    elif job.info['status'] not in ['Finished', 'Error'] and job_id not in run_jobs_info['job_ids']:
        job.info['status'] = 'Aborted'
    fns = find_files_by_ending(job_dir, '.sqlite')
    if len(fns) > 0:
        db_path = os.path.join(job_dir, fns[0])
    else:
        db_path = ''
    job_viewable = os.path.exists(db_path)
    job.set_info_values(
        viewable=job_viewable,
        db_path=db_path,
        status=job.info['status'],
    )
    existing_reports = []
    for report_type in get_valid_report_types():
        report_path = await filerouter.job_report(request, job_id, report_type)
        if report_path is not None and os.path.exists(report_path):
            existing_reports.append(report_type)
    job.set_info_values(reports=existing_reports)
    return job

'''
async def get_jobs_details (request):
    queries = request.rel_url.query
    ids = int(queries['jobids'])
    all_jobs = []
    for job_id in ids:
        try:
            job = await get_job(job_id, request)
            if job is None:
                continue
            all_jobs.append(job)
        except:
            traceback.print_exc()
            continue
    return web.json_response([job.get_info_dict() for job in all_jobs])
'''

async def get_jobs (request):
    global filerouter
    jobs_dirs = await filerouter.get_jobs_dirs(request)
    jobs_dir = jobs_dirs[0]
    if jobs_dir is None:
        return web.json_response([])
    if os.path.exists(jobs_dir) == False:
        os.makedirs(jobs_dir)
    queries = request.rel_url.query
    ids = json.loads(queries['ids'])
    jobs = []
    for job_id in ids:
        try:
            job = await get_job(request, job_id)
            if job is not None:
                jobs.append(job)
        except:
            traceback.print_exc()
            continue
    return web.json_response([job.get_info_dict() for job in jobs])

async def get_all_jobs (request):
    global servermode
    if servermode:
        r = await cravatserver.is_loggedin(request)
        if r == False:
            return web.json_response({'status': 'notloggedin'})
    global filerouter
    jobs_dirs = await filerouter.get_jobs_dirs(request)
    if jobs_dirs is None:
        return web.json_response([])
    all_jobs = []
    for jobs_dir in jobs_dirs:
        if os.path.exists(jobs_dir) == False:
            os.makedirs(jobs_dir)
        dir_it = os.scandir(jobs_dir)
        direntries = [de for de in dir_it]
        de_names = []
        for it in direntries:
            '''
            status_glob = glob.glob(os.path.join(it.path, '*.status.json'))
            print('glob=', status_glob)
            if len(status_glob) > 0:
                de_names.append(it.name)
            '''
            de_names.append(it.name)
        all_jobs.extend(de_names)
        '''
        for job_id in de_names:
            try:
                job = await get_job(request, job_id)
                if job is None:
                    continue
                all_jobs.append(job)
            except:
                traceback.print_exc()
                continue
        '''
    all_jobs.sort(reverse=True)
    return web.json_response(all_jobs)

async def view_job(request):
    global VIEW_PROCESS
    global filerouter
    job_id = request.match_info['job_id']
    db_path = await filerouter.job_db(request, job_id)
    if os.path.exists(db_path):
        if type(VIEW_PROCESS) == subprocess.Popen:
            VIEW_PROCESS.kill()
        VIEW_PROCESS = subprocess.Popen(['cravat-view', db_path])
        return web.Response()
    else:
        return web.Response(status=404)

async def get_job_status (request):
    global filerouter
    job_id = request.match_info['job_id']
    status_path = await filerouter.job_status_path(request, job_id)
    f = open(status_path)
    status = yaml.load(f)
    f.close()
    return web.json_response(status)

async def download_db(request):
    global filerouter
    job_id = request.match_info['job_id']
    db_path = await filerouter.job_db(request, job_id)
    db_fname = job_id+'.sqlite'
    headers = {'Content-Disposition': 'attachment; filename='+db_fname}
    return web.FileResponse(db_path, headers=headers)

async def get_job_log (request):
    global filerouter
    job_id = request.match_info['job_id']
    log_path = await filerouter.job_log(request, job_id)
    if log_path is not None:
        with open(log_path) as f:
            return web.Response(text=f.read())
    else:
        return web.Response(text='log file does not exist.')

def get_valid_report_types():
    reporter_infos = au.get_local_module_infos(types=['reporter'])
    report_types = [x.name.split('reporter')[0] for x in reporter_infos]
    return report_types

def get_report_types(request):
    global cfl
    default_reporter = cfl.get_cravat_conf_value('reporter')
    default_type = default_reporter.split('reporter')[0]
    valid_types = get_valid_report_types()
    return web.json_response({'valid': valid_types, 'default': default_type})

async def generate_report(request):
    global filerouter
    job_id = request.match_info['job_id']
    report_type = request.match_info['report_type']
    if report_type in get_valid_report_types():
        job_input = await filerouter.job_run_path(request, job_id)
        run_args = ['cravat', job_input]
        run_args.extend(['--startat', 'reporter'])
        run_args.extend(['--repeat', 'reporter'])
        run_args.extend(['-t', report_type])
        p = subprocess.Popen(run_args)
        p.wait()
    return web.Response()

async def download_report(request):
    global filerouter
    job_id = request.match_info['job_id']
    report_type = request.match_info['report_type']
    report_path = await filerouter.job_report(request, job_id, report_type) 
    report_name = job_id+'.'+report_path.split('.')[-1]
    headers = {'Content-Disposition':'attachment; filename='+report_name}
    return web.FileResponse(report_path, headers=headers)

def get_jobs_dir (request):
    jobs_dir = au.get_jobs_dir()
    return web.json_response(jobs_dir)

def set_jobs_dir (request):
    queries = request.rel_url.query
    d = queries['jobsdir']
    au.set_jobs_dir(d)
    return web.json_response(d)

async def get_system_conf_info (request):
    info = au.get_system_conf_info(json=True)
    return web.json_response(info)

async def update_system_conf (request):
    global servermode
    if servermode:
        username = await cravatserver.get_username(request)
        if username != 'admin':
            return web.json_response({'success': False, 'msg': 'Only admin can change the settings.'})
        r = await cravatserver.is_loggedin(request)
        if r == False:
            return web.json_response({'success': False, 'mgs': 'Only logged-in admin can change the settings.'})
    queries = request.rel_url.query
    sysconf = json.loads(queries['sysconf'])
    try:
        success = au.update_system_conf_file(sysconf)
        if 'modules_dir' in sysconf:
            modules_dir = sysconf['modules_dir']
            cravat_yml_path = os.path.join(modules_dir, 'cravat.yml')
            if os.path.exists(cravat_yml_path) == False:
                au.set_modules_dir(modules_dir)
        global job_queue
        qitem = {
            'cmd': 'set_max_num_concurrent_jobs', 
            'max_num_concurrent_jobs': sysconf['max_num_concurrent_jobs']
        }
        job_queue.put(qitem)
    except:
        raise
        sysconf = {}
        success = False
    return web.json_response({'success': success, 'sysconf': sysconf})

def reset_system_conf (request):
    d = au.read_system_conf_template()
    md = au.get_modules_dir()
    jobs_dir = au.get_jobs_dir()
    d['modules_dir'] = md
    d['jobs_dir'] = jobs_dir
    au.write_system_conf_file(d)
    return web.json_response({'status':'success', 'dict':yaml.dump(d)})

def get_servermode (request):
    global servermode
    return web.json_response({'servermode': servermode})

async def get_package_versions(request):
    cur_ver = au.get_current_package_version()
    lat_ver = au.get_latest_package_version()
    if lat_ver is not None:
        update = LooseVersion(lat_ver) > LooseVersion(cur_ver)
    else:
        update = False
    d = {
        'current': cur_ver,
        'latest': lat_ver,
        'update': update
    }
    return web.json_response(d)

def open_terminal (request):
    filedir = os.path.dirname(os.path.abspath(__file__))
    python_dir = os.path.dirname(sys.executable)
    p = sys.platform
    if p.startswith('win'):
        cmd = {'cmd': ['start', 'cmd'], 'shell': True}
    elif p.startswith('darwin'):
        cmd = {'cmd': '''
osascript -e 'tell app "Terminal"
do script "export PATH=''' + python_dir + ''':$PATH"
do script "echo Welcome to OpenCRAVAT" in window 1
end tell'
''', 'shell': True}
    elif p.startswith('linux'):
        p2 = platform.platform()
        if p2.startswith('Linux') and 'Microsoft' in p2:
            cmd = {'cmd': ['ubuntu1804.exe'], 'shell': True}
        else:
            return
    else:
        return
    subprocess.call(cmd['cmd'], shell=cmd['shell'])
    response = 'done'
    return web.json_response(response)

def get_last_assembly (request):
    last_assembly = au.get_last_assembly()
    return web.json_response(last_assembly)

async def delete_job (request):
    global job_queue
    job_id = request.match_info['job_id']
    global filerouter
    job_dir = await filerouter.job_dir(request, job_id)
    qitem = {'cmd': 'delete', 'job_id': job_id, 'job_dir': job_dir}
    job_queue.put(qitem)
    while True:
        if os.path.exists(job_dir) == False:
            break
        else:
            await asyncio.sleep(0.5)
    return web.Response()

system_conf = au.get_system_conf()
if 'max_num_concurrent_jobs' not in system_conf:
    max_num_concurrent_jobs = constants.default_max_num_concurrent_jobs
    system_conf['max_num_concurrent_jobs'] = max_num_concurrent_jobs
    au.write_system_conf_file(system_conf)
else:
    max_num_concurrent_jobs = system_conf['max_num_concurrent_jobs']
job_worker = None
job_queue = None
run_jobs_info = None
def start_worker ():
    global job_worker
    global job_queue
    global run_jobs_info
    job_queue = Queue()
    run_jobs_info = Manager().dict()
    if job_worker == None:
        job_worker = Process(target=fetch_job_queue, args=(job_queue, run_jobs_info))
        job_worker.start()

def fetch_job_queue (job_queue, run_jobs_info):
    class JobTracker (object):
        def __init__(self, main_loop):
            self.running_jobs = {}
            self.queue = []
            global system_conf
            self.max_num_concurrent_jobs = int(system_conf['max_num_concurrent_jobs'])
            self.run_args = {}
            self.run_jobs_info = run_jobs_info
            self.run_jobs_info['job_ids'] = []
            self.loop = main_loop

        def add_job(self, qitem):
            self.queue.append(qitem['job_id'])
            self.run_args[qitem['job_id']] = qitem['run_args']

        def get_process(self, uid):
            # Return the process for a job
            return self.running_jobs.get(uid)

        async def cancel_job(self, uid):
            p = self.running_jobs.get(uid)
            p.poll()
            if p:
                if platform.platform().lower().startswith('windows'):
                    # proc.kill() doesn't work well on windows
                    subprocess.Popen("TASKKILL /F /PID {pid} /T".format(pid=p.pid),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    while True:
                        await asyncio.sleep(0.25)
                        if p.poll() is not None:
                            break
                else:
                    p.kill()
            self.clean_jobs(id)

        def clean_jobs(self, uid):
            # Clean up completed jobs
            to_del = []
            for uid, p in self.running_jobs.items():
                if p.poll() is not None:
                    to_del.append(uid)
            for uid in to_del:
                del self.running_jobs[uid]
                job_ids = self.run_jobs_info['job_ids']
                job_ids.remove(uid)
                self.run_jobs_info['job_ids'] = job_ids

        def list_running_jobs(self):
            # List currently tracked jobs
            return list(self.running_jobs.keys())

        def run_available_jobs (self):
            num_available_slot = self.max_num_concurrent_jobs - len(self.running_jobs)
            if num_available_slot > 0 and len(self.queue) > 0:
                for i in range(num_available_slot):
                    if len(self.queue) > 0:
                        job_id = self.queue.pop(0)
                        run_args = self.run_args[job_id]
                        del self.run_args[job_id]
                        p = subprocess.Popen(run_args)
                        self.running_jobs[job_id] = p

        async def delete_job (self, qitem):
            global filerouter
            job_id = qitem['job_id']
            if self.get_process(job_id) is not None:
                print('\nKilling job {}'.format(job_id))
                await self.cancel_job(job_id)
            job_dir = qitem['job_dir']
            if os.path.exists(job_dir):
                shutil.rmtree(job_dir)

        def set_max_num_concurrent_jobs (self, qitem):
            value = qitem['max_num_concurrent_jobs']
            try:
                self.max_num_concurrent_jobs = int(value)
            except:
                print('Invalid maximum number of concurrent jobs [{}]'.format(value))

    async def job_worker_main ():
        while True:
            job_tracker.clean_jobs(None)
            job_tracker.run_available_jobs()
            try:
                qitem = job_queue.get_nowait()
                cmd = qitem['cmd']
                if cmd == 'submit':
                    job_tracker.add_job(qitem)
                elif cmd == 'delete':
                    await job_tracker.delete_job(qitem)
                elif cmd == 'set_max_num_concurrent_jobs':
                    job_tracker.set_max_num_concurrent_jobs(qitem)
            except Empty:
                pass
            finally:
                await asyncio.sleep(1)

    main_loop = asyncio.new_event_loop()
    job_tracker = JobTracker(main_loop)
    main_loop.run_until_complete(job_worker_main())

async def redirect_to_index (request):
    global servermode
    if servermode:
        url = '/server/nocache/login.html'
    else:
        url = '/submit/index.html'
    return web.HTTPFound(url)

async def load_live_modules ():
    global live_modules
    global live_mapper
    global include_live_modules
    global exclude_live_modules
    print('populating live annotators')
    conf = au.get_system_conf()
    if 'live' in conf:
        live_conf = conf['live']
        if 'include' in live_conf:
            include_live_modules = live_conf['include']
        else:
            include_live_modules = []
        if 'exclude' in live_conf:
            exclude_live_modules = live_conf['exclude']
        else:
            exclude_live_modules = []
    else:
        include_live_modules = []
        exclude_live_modules = []
    live_modules = {}
    cravat_conf = au.get_cravat_conf()
    if 'genemapper' in cravat_conf:
        default_mapper = cravat_conf['genemapper']
    else:
        default_mapper = 'hg38'
    live_mapper = await get_live_mapper(default_mapper)
    modules = au.get_local_by_type(['annotator'])
    for module in modules:
        if module.name in exclude_live_modules:
            continue
        if len(include_live_modules) > 0 and module.name not in include_live_modules:
            continue
        annotator = await get_live_annotator(module.name)
        if annotator is None:
            continue
        live_modules[module.name] = annotator
    print('done populating live annotators')

async def live_annotate (input_data, annotators):
    from cravat.constants import mapping_parser_name
    from cravat.constants import all_mappings_col_name
    from cravat.inout import AllMappingsParser
    global live_modules
    global live_mapper
    response = {}
    crx_data, alt_transcripts = live_mapper.map(input_data)
    crx_data[mapping_parser_name] = AllMappingsParser(crx_data[all_mappings_col_name])
    for k, v in live_modules.items():
        if annotators is not None and k not in annotators:
            continue
        try:
            response[k] = v.annotate(input_data=crx_data)
        except Exception as e:
            import traceback
            traceback.print_exc()
            response[k] = None
    del crx_data[mapping_parser_name]
    response['crx'] = crx_data
    return response

async def get_live_annotation (request):
    queries = request.rel_url.query
    chrom = queries['chrom']
    pos = queries['pos']
    ref_base = queries['ref_base']
    alt_base = queries['alt_base']
    if 'uid' not in queries:
        uid = 'noid'
    else:
        uid = queries['uid']
    input_data = {'uid': uid, 'chrom': chrom, 'pos': int(pos), 'ref_base': ref_base, 'alt_base': alt_base}
    if 'annotators' in queries:
        annotators = queries['annotators'].split(',')
    else:
        annotators = None
    global live_modules
    global mapper
    if live_modules is None:
        await load_live_modules()
        response = await live_annotate(input_data, annotators)
    else:
        response = await live_annotate(input_data, annotators)
    return web.json_response(response)

filerouter = FileRouter()
VIEW_PROCESS = None
live_modules = None
include_live_modules = None
exclude_live_modules = None
live_mapper = None

routes = []
routes.append(['POST','/submit/submit',submit])
routes.append(['GET','/submit/annotators',get_annotators])
routes.append(['GET','/submit/jobs',get_all_jobs])
routes.append(['GET','/submit/jobs/{job_id}',view_job])
routes.append(['DELETE','/submit/jobs/{job_id}',delete_job])
routes.append(['GET','/submit/jobs/{job_id}/db', download_db])
routes.append(['GET','/submit/reports',get_report_types])
routes.append(['POST','/submit/jobs/{job_id}/reports/{report_type}',generate_report])
routes.append(['GET','/submit/jobs/{job_id}/reports/{report_type}',download_report])
routes.append(['GET','/submit/jobs/{job_id}/log',get_job_log])
routes.append(['GET', '/submit/getjobsdir', get_jobs_dir])
routes.append(['GET', '/submit/setjobsdir', set_jobs_dir])
routes.append(['GET', '/submit/getsystemconfinfo', get_system_conf_info])
routes.append(['GET', '/submit/updatesystemconf', update_system_conf])
routes.append(['GET', '/submit/resetsystemconf', reset_system_conf])
routes.append(['GET', '/submit/servermode', get_servermode])
routes.append(['GET', '/submit/packageversions', get_package_versions])
routes.append(['GET', '/submit/openterminal', open_terminal])
routes.append(['GET', '/submit/lastassembly', get_last_assembly])
routes.append(['GET', '/submit/getjobs', get_jobs])
routes.append(['GET', '/submit/annotate', get_live_annotation])
routes.append(['GET', '/', redirect_to_index])
routes.append(['GET', '/submit/jobs/{job_id}/status', get_job_status])

if __name__ == '__main__':
    app = web.Application()
    for route in routes:
        method, path, func_name = route
        app.router.add_route(method, path, func_name)
    web.run_app(app, port=8060)
