class WebSocketHandlers:
    def __init__(self, system_worker_state=None, wss=None, logger=None):
        self.routes = []
        self.system_worker_state = system_worker_state
        self.wss = wss
        self.logger = logger
        self.add_routes()

    def add_routes(self):
        self.routes = []
        self.routes.append(["GET", "/ws", self.connect_websocket])

    async def connect_websocket(self, request):
        import asyncio
        from aiohttp.web import WebSocketResponse
        import concurrent.futures
        from uuid import uuid4
        from .consts import WS_COOKIE_KEY
        assert self.system_worker_state is not None
        ws_id = request.cookies.get(WS_COOKIE_KEY)
        print(f"@ connect_websocket. cookies={request.cookies}")
        print(f"@ old ws_id={ws_id}")
        if ws_id and ws_id in self.wss:
            del self.wss[ws_id]
        ws_id = str(uuid4())
        print(f"@ new ws_id={ws_id}")
        ws = WebSocketResponse(timeout=60 * 60 * 24 * 365)
        self.wss[ws_id] = ws
        await ws.prepare(request)
        try:
            await ws.send_json({"msg_kind": "connection", WS_COOKIE_KEY: ws_id})
        except ConnectionResetError:
            raise
        except:
            raise
        to_dels = []
        for ws_id in self.wss:
            ws_t = self.wss[ws_id]
            if ws_t.closed:
                to_dels.append(ws_id)
        for ws_id in to_dels:
            del self.wss[ws_id]
        print(f"@ wss={self.wss}")
        while True:
            try:
                await asyncio.sleep(1)
                if ws.closed:
                    break
                await self.process_system_worker_state(ws=ws)
            except concurrent.futures._base.CancelledError:
                pass
            except ConnectionResetError:
                break
            except Exception as e:
                if self.logger:
                    self.logger.exception(e)
        return ws

    def empty_list_proxy(self, l):
        for _ in l:
            l.pop()

    async def process_setup_state(self, ws=None):
        from .consts import SYSTEM_STATE_SETUP_KEY
        from .consts import SYSTEM_MSG_KEY
        if ws is None or not self.system_worker_state:
            return
        if SYSTEM_STATE_SETUP_KEY not in self.system_worker_state:
            return
        data = self.system_worker_state[SYSTEM_STATE_SETUP_KEY]
        for msg in data.get("message"):
            await ws.send_json({SYSTEM_MSG_KEY: SYSTEM_STATE_SETUP_KEY, "msg": msg})
        self.empty_list_proxy(data.get("message"))

    async def process_install_state(self, ws=None):
        from .consts import SYSTEM_STATE_INSTALL_KEY
        if ws is None or not self.system_worker_state:
            return
        if SYSTEM_STATE_INSTALL_KEY not in self.system_worker_state:
            return
        install_datas = self.system_worker_state[SYSTEM_STATE_INSTALL_KEY]
        for _, data in install_datas.items():
            await ws.send_json(data)
        await self.delete_done_install_states()
    async def delete_done_install_states(self):
        from .consts import SYSTEM_STATE_INSTALL_KEY
        if SYSTEM_STATE_INSTALL_KEY not in self.system_worker_state:
            return
        install_datas = self.system_worker_state[SYSTEM_STATE_INSTALL_KEY]
        to_del = []
        for module_name, data in install_datas:
            if data["stage"] in ["finish", "error", "skip", "killed"]:
                to_del.append(module_name)
        for module_name in to_del:
            del install_datas[module_name]

    async def process_system_worker_state(self, ws=None):
        if ws is None:
            return
        await self.process_setup_state(ws=ws)
        await self.process_install_state(ws=ws)

