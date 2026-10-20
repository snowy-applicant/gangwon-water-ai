import unittest,tempfile,threading,json
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from waterai import server
class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.old_db=server.DB;cls.old_token=server.TOKEN
        server.DB=Path(cls.temp.name)/'test.sqlite3';server.TOKEN='test-token-only-not-for-deployment'
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.http.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.temp.cleanup();server.DB=cls.old_db;server.TOKEN=cls.old_token
    def req(self,path,data=None,token=None,origin=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        if origin:headers['Origin']=origin
        req=Request(self.base+path,data=json.dumps(data).encode() if data else None,headers=headers)
        try:
            with urlopen(req,timeout=10) as response:return response.status,json.load(response)
        except HTTPError as e:return e.code,json.load(e)
    def test_admin_auth(self):
        self.assertEqual(self.req('/api/reports')[0],401)
        self.assertEqual(self.req('/api/reports',token='bad')[0],401)
        self.assertEqual(self.req('/api/reports',token=server.TOKEN)[0],200)
    def test_report_dedupe_and_privacy(self):
        data=dict(region_id='51110',severity=3,text='시험 신고',reporter='test-client')
        code,body=self.req('/api/reports',data);self.assertEqual(code,201);self.assertNotIn('text',body)
        self.assertEqual(self.req('/api/reports',data)[0],409)
        code,rows=self.req('/api/reports',token=server.TOKEN)
        self.assertEqual(rows[0]['score'],90);self.assertNotIn('reporter',rows[0])
    def test_cross_origin_and_state(self):
        data=dict(region_id='51110',severity=1,text='시험',reporter='other')
        self.assertEqual(self.req('/api/reports',data,origin='https://other.invalid')[0],403)
        self.assertEqual(self.req('/api/plans',dict(id='test-plan',status='승인'),token=server.TOKEN)[0],200)
        self.assertEqual(self.req('/api/plans',token=server.TOKEN)[1][0]['status'],'승인')
if __name__=='__main__':unittest.main()
