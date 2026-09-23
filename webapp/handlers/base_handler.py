#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tornado.web

class BaseHandler(tornado.web.RequestHandler):
    """全局统一的基础 Handler，提供 CORS 与标准 JSON 响应"""
    def set_default_headers(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type, X-Requested-With")

    def options(self, *args, **kwargs):
        self.set_status(204)
        self.finish()

    def write_json(self, success=True, msg="", data=None, **kwargs):
        resp = {"success": success, "msg": msg}
        if data is not None:
            resp["data"] = data
        resp.update(kwargs)
        self.write(resp)
