"""

This Molotov script has 2 scenario

"""
from molotov import setup, global_setup, scenario

_HEADERS = {}

_API = 'http://localhost:8080/'


@global_setup()
def init_test(args):
    _HEADERS['Authorization'] = 'Basic cm9vdDpyb290'


@setup()
async def init_worker(worker_id, args):
    return {'headers': _HEADERS}


@scenario(weight=50)
async def scenario_one(session):
    async with session.get(_API) as resp:
        #res = await resp.json()
        #assert res
        assert resp.status == 200


@scenario(weight=50)
async def scenario_two(session):
    async with session.get(_API) as resp:
        assert resp.status == 200
