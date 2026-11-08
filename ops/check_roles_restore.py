#!/usr/bin/env python3
"""Restore Monitor globals and DB in a networkless disposable PG. Safe aggregates only."""
import fcntl,hashlib,json,os,time,uuid,subprocess
from urllib.parse import urlsplit,unquote
from pathlib import Path
import autonomy
ROOT=autonomy.ROOT
def psql(container,user,database,query):
    return autonomy.run(["docker","exec",container,"psql","-U",user,"-d",database,
        "-v","ON_ERROR_STOP=1","-Atc",query],timeout=40).decode().strip()
def snapshot(container,user,database,bootstrap):
    where="rolname NOT LIKE 'pg_%' AND rolname <> '"+bootstrap+"'"
    roles=psql(container,user,database,"SELECT coalesce(jsonb_agg(jsonb_build_array(rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil,rolconfig) ORDER BY rolname),'[]') FROM pg_roles WHERE "+where)
    permissions=psql(container,user,database,"SELECT coalesce(jsonb_agg(jsonb_build_array(rolname,has_table_privilege(oid,'public.messages','SELECT'),has_table_privilege(oid,'public.messages','UPDATE'),has_column_privilege(oid,'public.tm_discovered_sources','status','UPDATE')) ORDER BY rolname),'[]') FROM pg_roles WHERE "+where)
    members=psql(container,user,database,"SELECT coalesce(jsonb_agg(jsonb_build_array(roleid::regrole::text,member::regrole::text,admin_option) ORDER BY roleid::regrole::text,member::regrole::text),'[]') FROM pg_auth_members")
    return {"roles":json.loads(roles),"permissions":json.loads(permissions),"members":json.loads(members)}

def auth_query(container,role,password):
    if not password or "\n" in password or "\r" in password:
        raise RuntimeError("unsupported_existing_password")
    command=["docker","exec","-i",container,"sh","-ec",
             "IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql -w -h 127.0.0.1 -U \"$1\" -d restore_check -v ON_ERROR_STOP=1 -Atc 'SELECT current_user'",
             "auth-probe",role]
    result=subprocess.run(command,input=(password+"\n").encode(),capture_output=True,timeout=20)
    return result.returncode==0 and result.stdout.decode().strip()==role
def existing_credentials(source_env):
    credentials={}
    user=source_env.get("POSTGRES_USER","postgres")
    if source_env.get("POSTGRES_PASSWORD"):credentials[user]=source_env["POSTGRES_PASSWORD"]
    for container in ("tm-dashboard-v861-live-readonly","tm-v861-live-collector","tm-v861-live-worker"):
        env=dict(x.split("=",1) for x in autonomy.inspect(container)["Config"]["Env"] if "=" in x)
        if env.get("DB_USER") and env.get("DB_PASSWORD"):credentials[env["DB_USER"]]=env["DB_PASSWORD"]
        for name in ("DATABASE_URL","CLOUD_SOURCE_CONTROL_DATABASE_URL"):
            if env.get(name):
                parsed=urlsplit(env[name])
                if parsed.username and parsed.password:
                    credentials[unquote(parsed.username)]=unquote(parsed.password)
    return credentials


def due_decision(state,attempt,at):
    if state.get("password_auth_tested") and 0<=at-state.get("verified_at",0)<86400:
        return "fresh"
    if 0<=at-attempt.get("attempted_at",0)<21600:
        return "retry_limited"
    return "run"

def main():
    os.umask(0o077)
    assert not (ROOT/"PAUSE").exists()
    with (ROOT/"lock").open("a") as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print("MONITOR_ROLES_BUSY");return
        state_path=ROOT/"roles-daily-status.json"
        prior=json.loads(state_path.read_text()) if state_path.exists() else {}
        attempt_path=ROOT/"roles-daily-attempt.json"
        attempt=json.loads(attempt_path.read_text()) if attempt_path.exists() else {}
        decision=due_decision(prior,attempt,time.time())
        if decision=="fresh":
            print("MONITOR_ROLES_DAILY_FRESH");return
        if decision=="retry_limited":raise RuntimeError("roles_retry_limited")
        attempt_path.write_text(json.dumps({"attempted_at":time.time()}))
        source=autonomy.inspect(autonomy.DB)
        env=dict(x.split("=",1) for x in source["Config"]["Env"] if "=" in x)
        source_user=env.get("POSTGRES_USER","postgres");source_db=env.get("POSTGRES_DB",source_user)
        backup=autonomy.backup_and_restore(ROOT,source["Image"])
        folder=max((ROOT/"backups").glob("*/verified.json"),key=lambda p:p.stat().st_mtime).parent
        assert json.loads((folder/"verified.json").read_text())["verified_at"]==backup["verified_at"]
        hashes=json.loads((folder/"sha256.json").read_text())
        for name in ("database.dump","roles.sql"):
            assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==hashes[name]
        name="tm-roles-restore-"+uuid.uuid4().hex[:12]
        bootstrap="tm_restore_"+uuid.uuid4().hex[:12]
        before=snapshot(autonomy.DB,source_user,source_db,bootstrap)
        made=False
        try:
            autonomy.run(["docker","create","--name",name,"--label","tm.roles.restore=true",
                "--network","none","--restart","no","--memory","256m","--cpus","0.5",
                "--tmpfs","/var/lib/postgresql/data:rw,size=128m",
                "-e","POSTGRES_HOST_AUTH_METHOD=trust","-e","POSTGRES_USER="+bootstrap,
                "-e","POSTGRES_DB=restore_check",source["Image"]])
            made=True;autonomy.run(["docker","start",name])
            for _ in range(60):
                try:
                    autonomy.run(["docker","exec",name,"pg_isready","-U",bootstrap,"-d","restore_check"],timeout=5);break
                except autonomy.SafeError:time.sleep(1)
            else:raise RuntimeError("restore_start_timeout")
            with (folder/"roles.sql").open("rb") as data:
                autonomy.run(["docker","exec","-i",name,"psql","-U",bootstrap,"-d","restore_check",
                              "-v","ON_ERROR_STOP=1","-q"],stdin=data,timeout=120)
            with (folder/"database.dump").open("rb") as data:
                autonomy.run(["docker","exec","-i",name,"pg_restore","-U",bootstrap,"-d","restore_check",
                              "--exit-on-error"],stdin=data,timeout=180)
            restored=snapshot(name,bootstrap,"restore_check",bootstrap)
            assert restored==before,"role_or_permission_mismatch"
            count=int(psql(name,bootstrap,"restore_check","SELECT count(*) FROM public.messages"))
            assert count==backup["messages"]
            # Unix-socket trust login checks rolcanlogin without reading any password.
            logins=0
            for role in restored["roles"]:
                if role[5]:
                    assert psql(name,role[0],"restore_check","SELECT current_user")==role[0]
                    logins+=1
            checked_operations=0
            login_names={role[0] for role in restored["roles"] if role[5]}
            for role,can_read,can_update_messages,can_update_status in restored["permissions"]:
                if role not in login_names:continue
                for query,expected in [
                    ("SELECT count(*) FROM public.messages",can_read),
                    ("UPDATE public.messages SET text=text WHERE false",can_update_messages),
                    ("UPDATE public.tm_discovered_sources SET status=status WHERE false",can_update_status)]:
                    try:
                        psql(name,role,"restore_check",query)
                        permitted=True
                    except autonomy.SafeError:
                        permitted=False
                    assert permitted==expected,"actual_permission_check_failed"
                    checked_operations+=1
            # Change only the disposable PG hba, never source access or credentials.
            autonomy.run(["docker","exec",name,"sh","-ec",
                'printf "%s\\n" "local all all trust" "host all all 127.0.0.1/32 scram-sha-256" > "$PGDATA/pg_hba.conf"'])
            psql(name,bootstrap,"restore_check","SELECT pg_reload_conf()")
            assert psql(name,bootstrap,"restore_check","SELECT count(*) FROM pg_hba_file_rules WHERE type='host' AND auth_method='scram-sha-256' AND error IS NULL")=="1"
            credentials=existing_credentials(env)
            password_logins=0
            for role in restored["roles"]:
                if not role[5]:continue
                assert role[0] in credentials,"existing_role_credentials_unavailable"
                assert auth_query(name,role[0],credentials[role[0]]),"password_auth_failed"
                assert not auth_query(name,role[0],uuid.uuid4().hex),"wrong_password_accepted"
                password_logins+=1
            credentials.clear()
            proof={"password_logins":password_logins,"wrong_password_rejections":password_logins,"actual_read_write_checks":checked_operations,"verified_at":time.time(),"messages":count,"role_count":len(restored["roles"]),
                   "role_attributes_match":True,"memberships_match":True,"permission_matrix_match":True,
                   "local_trust_logins":logins,"password_auth_tested":True,
                   "roles_restore_verified":True,"production_changed":False}
            (folder/"roles-restore-verified.json").write_text(json.dumps(proof))
            state_path.write_text(json.dumps(proof))
            print("MONITOR_ROLES_RESTORE="+json.dumps(proof,sort_keys=True),flush=True)
        finally:
            if made:autonomy.run(["docker","rm","-f","-v",name])
if __name__=="__main__":
    try:main()
    except Exception as exc:
        print("MONITOR_ROLES_RESTORE_FAILED="+type(exc).__name__);raise SystemExit(1)
