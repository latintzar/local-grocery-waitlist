import importlib
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setenv('VITRINA_GUEST','1')
    import app
    importlib.reload(app)
    with TestClient(app.app,base_url='https://example.test') as a, TestClient(app.app,base_url='https://example.test') as b:
        a.get('/');b.get('/');yield a,b

def test_native_signup_quiz_and_per_visitor_isolation(clients):
    a,b=clients;origin={'Origin':'https://example.test'}
    signup=a.post('/api/waitlist/signup',headers=origin,json={'phone':'+359111111111','first_name':'Sample','city':'Sofia','home_address':'Sample Road 1','lang':'en'})
    assert signup.status_code==200
    token=signup.json()['token']
    assert a.get('/api/waitlist/position/'+token).status_code==200
    assert b.get('/api/waitlist/position/'+token).status_code==404
    answer=a.post('/api/waitlist/answer',headers=origin,json={'token':token,'key':'buy','value':['bread']})
    assert answer.status_code==200 and answer.json()['answers']['buy']==['bread']
    assert a.post('/api/waitlist/lookup',headers={'Origin':'https://other.test'},json={'phone':'+359111111111'}).status_code==403
    assert a.post('/api/geocode/forward',headers=origin,json={'q':'Sample Road','lang':'en'}).status_code==503

def test_guest_cookie_and_email_transport_are_bounded(clients,monkeypatch):
    a,_=clients;response=a.get('/')
    from product.backend.api.routes import waitlist
    monkeypatch.setenv('RESEND_API_KEY','synthetic-secret')
    assert waitlist._resend_send(to='sample@example.test',subject='test',html='',text='')['error']=='guest_external_actions_disabled'
    fresh=TestClient(a.app,base_url='https://example.test')
    cookie=fresh.get('/').headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=strict' in cookie
