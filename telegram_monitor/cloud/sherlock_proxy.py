"""Authenticated dashboard proxy for manually started Sherlock username checks.

No requests to Sherlock in the collector, worker or event classification pipeline.
"""
import json
import os
import re
import urllib.error
import urllib.request
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, field_validator

router = APIRouter(prefix='/api/osint/sherlock', tags=['osint'])
_JOB = re.compile(r'^[a-f0-9]{32}$')
_USER = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,31}$')


class NewJob(BaseModel):
    username: str

    @field_validator('username')
    @classmethod
    def clean_name(cls, value):
        value = value.strip().removeprefix('@')
        if not _USER.fullmatch(value):
            raise ValueError('Username must have 3-32 ASCII characters (letters, numbers, _, -, .)')
        return value


def _enabled():
    if os.environ.get('SHERLOCK_OSINT_ENABLED','').lower() not in ('1','true','yes','on'):
        raise HTTPException(503, detail='Sherlock is disabled')


def _request(method, path, payload=None):
    _enabled()
    token = os.getenv('SHERLOCK_WORKER_TOKEN', '')
    if len(token)<32:
        raise HTTPException(503, detail='Sherlock is not configured')
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    url = 'http://sherlock_osint:8099' + path  # static internal origin; no user-controlled host
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': 'Bearer '+token, 'Content-Type':'application/json', 'Accept':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        # A bridged 429 must remain distinguishable from temporary sidecar failures.
        if exc.code == 429:
            raise HTTPException(429, detail='Only one Sherlock search can run at a time') from exc
        if exc.code == 422:
            raise HTTPException(422, detail='Invalid Sherlock search request') from exc
        raise HTTPException(503, detail='Sherlock service unavailable') from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(503, detail='Sherlock service unavailable') from exc


def _same_origin_json(request:Request):
    host=request.headers.get('host','')
    origin=request.headers.get('origin','')
    # Host is forwarded by uvicorn; tunnel is plain HTTP, not a public endpoint.
    if (not host or origin != 'http://'+host or
        request.headers.get('x-tm-action') != '1' or
        request.headers.get('content-type','').split(';',1)[0].strip().lower()!='application/json'):
        raise HTTPException(403, detail='Same-origin JSON request required')


@router.post('/jobs')
def start_job(job:NewJob, request:Request):
    _same_origin_json(request)
    return _request('POST','/jobs',{'username':job.username})


@router.get('/jobs/{job_id}')
def job_status(job_id:str):
    if not _JOB.fullmatch(job_id):
        raise HTTPException(422, detail='Invalid job ID')
    return _request('GET','/jobs/'+job_id)


@router.get('/recent')
def recent_jobs():
    return _request('GET','/recent')


# Advanced manual-only API. The worker validates every input before execution.
@router.get('/capabilities')
def advanced_capabilities():
    return _request('GET', '/capabilities')


@router.post('/v2/jobs')
def advanced_start(request: Request, payload: dict):
    _same_origin_json(request)
    if len(json.dumps(payload)) > 12000:
        raise HTTPException(413, detail='Search request too large')
    return _request('POST', '/v2/jobs', payload)


@router.get('/v2/jobs/{job_id}')
def advanced_status(job_id: str):
    if not _JOB.fullmatch(job_id):
        raise HTTPException(422, detail='Invalid job ID')
    return _request('GET', '/v2/jobs/' + job_id)


@router.get('/v2/recent')
def advanced_history():
    return _request('GET', '/v2/recent')


@router.get('/v2/jobs/{job_id}/export')
def advanced_export(job_id: str, format: str = 'csv'):
    if not _JOB.fullmatch(job_id) or format not in ('json', 'txt', 'csv', 'xlsx'):
        raise HTTPException(422, detail='Invalid export request')
    _enabled()
    token = os.getenv('SHERLOCK_WORKER_TOKEN', '')
    if len(token) < 32:
        raise HTTPException(503, detail='Sherlock is not configured')
    address = 'http://sherlock_osint:8099/v2/jobs/' + job_id + '/export?format=' + format
    req = urllib.request.Request(address, headers={'Authorization': 'Bearer ' + token})
    try:
        with urllib.request.urlopen(req, timeout=30) as reply:
            payload = reply.read(15 * 1024 * 1024 + 1)
            if len(payload) > 15 * 1024 * 1024:
                raise HTTPException(413, detail='Report too large')
            disposition = 'attachment; filename="sherlock-' + job_id + '.' + format + '"'
            return Response(payload, media_type=reply.headers.get('Content-Type',
                'application/octet-stream'), headers={
                'Content-Disposition': disposition, 'Cache-Control': 'no-store'})
    except HTTPException:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise HTTPException(409, detail='Search not finished') from exc
        raise HTTPException(503, detail='Sherlock export unavailable') from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, detail='Sherlock export unavailable') from exc
