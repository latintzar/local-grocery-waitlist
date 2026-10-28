"""Configure recipient-owned providers outside the checkout, or run the local server."""
import argparse,getpass,os,stat
from pathlib import Path

NAMES=('SUPABASE_URL','SUPABASE_SERVICE_ROLE_KEY','MAPBOX_TOKEN','RESEND_API_KEY','ANTHROPIC_API_KEY')

def read_private(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600 or info.st_uid!=os.geteuid() or info.st_nlink!=1:raise ValueError('Use an owned mode-0600 file')
    values={}
    for line in path.read_text().splitlines():
        name,sep,value=line.partition('=')
        if not sep or name not in NAMES or name in values:raise ValueError('Invalid configuration')
        values[name]=value
    return values

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['configure','doctor','serve']);parser.add_argument('--env-file',type=Path);parser.add_argument('--port',type=int,default=8000);parser.add_argument('--guest',action='store_true');args=parser.parse_args()
    if args.action=='configure':
        if not args.env_file:parser.error('Use --env-file outside the source tree')
        path=args.env_file.expanduser().absolute()
        if path.resolve().is_relative_to(Path(__file__).resolve().parent):parser.error('Keep configuration outside the checkout')
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        values={}
        for name in NAMES:
            value=getpass.getpass(f'{name} (blank to skip): ')
            if '\n' in value or '\r' in value:parser.error('Invalid value')
            values[name]=value
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:
            for name,value in values.items():f.write(f'{name}={value}\n')
        print('Private configuration saved.')
        return
    values=read_private(args.env_file.expanduser()) if args.env_file else {}
    if args.action=='doctor':
        print('Database configured:',bool(values.get('SUPABASE_URL') and values.get('SUPABASE_SERVICE_ROLE_KEY')))
        print('Optional providers configured:',sum(bool(values.get(k)) for k in NAMES[2:]))
        print('Provider calls: 0');return
    os.environ.update(values)
    if args.guest:os.environ['VITRINA_GUEST']='1'
    import uvicorn
    uvicorn.run('app:app',host='127.0.0.1',port=args.port,access_log=False)
if __name__=='__main__':main()
