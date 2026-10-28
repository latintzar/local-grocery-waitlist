"""Run the original Vitrina waitlist and landing against an owned database or isolated guest sessions."""
from __future__ import annotations
import asyncio, json, os, re, secrets, sys, time
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT=Path(__file__).resolve().parent
PUBLIC=ROOT/'product/public/bg'
sys.path.insert(0,str(ROOT/'product/backend/api'))
sys.path.insert(0,str(ROOT/'product/backend/mcp'))
from routes import waitlist,geocode
from guest_store import _BACKEND_CONTEXT

prefix=os.environ.get('VITRINA_BASE_PATH','').rstrip('/')
if prefix and not re.fullmatch(r'/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+',prefix):raise ValueError('Invalid base path')
guest=os.environ.get('VITRINA_GUEST')=='1'
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
app.include_router(waitlist.router,prefix=prefix+'/api')
app.include_router(geocode.router,prefix=prefix+'/api')
_sessions={};_lock=asyncio.Lock()
@app.middleware('http')
async def security(request:Request,call_next):
    if not guest:return await call_next(request)
    if request.method not in {'GET','HEAD'}:
        source=request.headers.get('origin','');host=request.headers.get('host','')
        if not source or urlsplit(source).netloc!=host or request.headers.get('sec-fetch-site','same-origin') not in {'same-origin','none'}:
            return Response(status_code=403)
        try:
            if int(request.headers.get('content-length','0') or '0')>16384:return Response(status_code=413)
        except ValueError:return Response(status_code=400)
        if len(await request.body())>16384:return Response(status_code=413)
    async with _lock:
        now=time.monotonic()
        for key in [k for k,v in _sessions.items() if now-v[0]>3600]:_sessions.pop(key,None)
        token=request.cookies.get('vitrina_guest','')
        fresh=not re.fullmatch(r'[A-Za-z0-9_-]{43}',token or '') or token not in _sessions
        if fresh:
            if len(_sessions)>=512:return Response(status_code=503)
            token=secrets.token_urlsafe(32);_sessions[token]=(now,{})
        _,data=_sessions[token];_sessions[token]=(now,data)
        context=_BACKEND_CONTEXT.set(data)
        try:response=await call_next(request)
        finally:_BACKEND_CONTEXT.reset(context)
        if fresh:response.set_cookie('vitrina_guest',token,httponly=True,secure=True,samesite='strict',path=(prefix or '')+'/')
        response.headers['Cache-Control']='no-store'
        return response

def rewrite(text):
    if not prefix:return text
    paths=r'/api/|/live/|/waitlist|/legal/|/i18n/|/favicon|/apple-touch-icon|/site\.webmanifest'
    return re.sub(paths,lambda match:prefix+match.group(),text)

GUEST_NOTICE='<div role="status" style="position:relative;z-index:200;background:#fff3dd;padding:12px 16px;font:600 14px Archivo,sans-serif">Demo: use sample details. Your session lasts up to one hour.</div>'
GUEST_LEGAL="Demo sessions are held privately in server memory for up to one hour. Use sample details."
@app.get(prefix+'/',response_class=HTMLResponse)
def home():
    html=(PUBLIC/'live/index.html').read_text()
    html=html.replace('</head>', '<style>@media(max-width:560px){.hero-title{font-size:clamp(25px,8vw,44px)}}</style></head>',1)
    if guest:
        html=html.replace('<body>', '<body>'+GUEST_NOTICE,1)
        html=html.replace('function openLegal(kind){', 'function openLegal(kind){ alert('+repr(GUEST_LEGAL)+'); return;')
    return HTMLResponse(rewrite(html))
@app.get(prefix+'/waitlist',response_class=HTMLResponse)
def waitlist_page():
    html=(PUBLIC/'waitlist/index.html').read_text()
    if guest:
        html=html.replace('<main class="stage" id="stage">', '<main class="stage" id="stage">'+GUEST_NOTICE,1)
        html=html.replace('</head>', '<style>#climb-call,#climb-follow-ig,.climb-card.invite{display:none!important}</style></head>',1)
        html=html.replace('async function geocodeForward(q, lang) {','async function geocodeForward(q, lang) { return {error:true,suggestions:[]};')
        html=html.replace('async function geocodeReverse(lng, lat, lang) {','async function geocodeReverse(lng, lat, lang) { return {error:true};')
    return HTMLResponse(rewrite(html))
@app.get(prefix+'/waitlist/')
def waitlist_slash():return waitlist_page()
@app.get(prefix+'/legal/legal-modal.js')
def legal_modal():
    if guest:return Response('window.VitrinaLegal={open:()=>alert('+repr(GUEST_LEGAL)+'),close:()=>{}};',media_type='text/javascript')
    return FileResponse(PUBLIC/'legal/legal-modal.js',media_type='text/javascript')
@app.get(prefix+'/live/i18n.v14.js')
def home_i18n():return Response(rewrite((PUBLIC/'live/i18n.v14.js').read_text()),media_type='text/javascript')
@app.get(prefix+'/i18n/strings.json')
def waitlist_strings():
    value=json.loads((PUBLIC/'i18n/strings.json').read_text())
    if guest:
        value.update({
          'wl.phone.sub':{'en':'Use a sample phone number for this demo.','bg':'Използвай примерен телефонен номер за демото.'},
          'wl.home.sub':{'en':'Use a sample address for this demo.','bg':'Използвай примерен адрес за демото.'},
          'wl.welcome.title':{'en':'Sample signup saved.','bg':'Примерната регистрация е запазена.'},
          'wl.welcome.body1':{'en':'Your sample signup is available in this browser session.','bg':'Примерната регистрация е достъпна в тази сесия.'},
          'wl.welcome.body2':{'en':'Explore the taste quiz and position screen.','bg':'Разгледай въпросите и позицията в списъка.'},
          'wl.welcome.footer':{'en':'Demo session','bg':'Демо сесия'}
        })
    return Response(json.dumps(value,ensure_ascii=False),media_type='application/json')
@app.get(prefix+'/i18n/i18n.js')
def wl_i18n():return Response(rewrite((PUBLIC/'i18n/i18n.js').read_text()),media_type='text/javascript')
app.mount(prefix+'/live',StaticFiles(directory=PUBLIC/'live'),name='live')
app.mount(prefix+'/i18n',StaticFiles(directory=PUBLIC/'i18n'),name='i18n')
app.mount(prefix+'/legal',StaticFiles(directory=PUBLIC/'legal'),name='legal')
app.mount(prefix+'/waitlist/assets',StaticFiles(directory=PUBLIC/'waitlist'),name='waitlist-assets')
@app.get(prefix+'/{asset}')
def root_asset(asset:str):
    if asset not in {'favicon.svg','favicon-32.png','apple-touch-icon.png','site.webmanifest'}:return Response(status_code=404)
    return FileResponse(PUBLIC/asset)
