#!/usr/bin/env python3
"""Operator setup. Credentials are handled by the official CLI, never this script."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from controlplane.config import load, local_path
from controlplane.feishu import Feishu
from controlplane.processes import atomic

CLI_VERSION='1.0.98'


def save(path, update):
    value=json.loads(path.read_text(encoding='utf-8'))
    update(value)
    atomic(path,value)


def check(adapter, config):
    status=adapter.call(['auth','status','--json','--verify'])
    if status.get('identities',{}).get('bot',{}).get('status')!='ready':
        raise ValueError('机器人身份未就绪；先完成官方 CLI 应用配置。')
    app=status.get('appId','')
    if config.get('app_id') and config['app_id']!=app:raise ValueError('CLI App ID 与 settings.json 不一致')
    info=adapter.call(['api','GET','/open-apis/bot/v3/info'])
    bot=info.get('bot',info)
    if not re.fullmatch(r'ou_[A-Za-z0-9_-]+',bot.get('open_id','')):raise ValueError('未取得机器人 open_id')
    if config.get('bot_id') and config['bot_id']!=bot['open_id']:raise ValueError('机器人 open_id 与 settings.json 不一致')
    return dict(app_id=app,bot_id=bot['open_id'],app_name=bot.get('app_name',''),brand=status.get('brand'))


def main():
    p=argparse.ArgumentParser(description='AnySolvers 飞书部署工具；不启动研究，不读取或输出密钥。')
    p.add_argument('action',choices=['prepare','new-app','existing-app','check','chats','members','configure','create-chat','enable','disable','send-test'])
    p.add_argument('--config',default=str(ROOT/'config/settings.json'))
    p.add_argument('--chat-id');p.add_argument('--user-id',action='append',default=[])
    p.add_argument('--name',default='AnySolvers 部署验收');p.add_argument('--request-id',help='Stable ID for idempotent group creation');p.add_argument('--reuse-auth-config',help='Explicitly reuse an existing deployment CLI authentication without copying secrets');p.add_argument('--receive-mode',choices=['stream','history'],default='stream');
    p.add_argument('--notify-user');p.add_argument('--label',default='AnySolvers 测试群')
    p.add_argument('--reply-to',help='测试群内一条真实消息的 om_ ID，仅 send-test 使用')
    args=p.parse_args();path=Path(args.config).resolve()
    if not path.exists():
        path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/'config/settings.example.json',path)
    if args.action=='prepare':
        node=shutil.which('node');npm=shutil.which('npm')
        if not node or not npm:raise ValueError('需要 Node >=22.19 和 npm')
        if tuple(map(int,subprocess.check_output([node,'--version'],text=True).strip().lstrip('v').split('.')[:2]))<(22,19):
            raise ValueError('需要 Node >=22.19')
        tools=local_path(load(path),'.state/tools/feishu');entry=tools/'node_modules/@larksuite/cli/bin'/('lark-cli.exe' if os.name=='nt' else 'lark-cli')
        if not entry.exists():
            npm_command=[npm]
            if os.name=='nt':
                npm_entry=Path(npm).parent/'node_modules/npm/bin/npm-cli.js'
                if not npm_entry.exists():raise ValueError('未找到 npm-cli.js，请使用 Node 官方安装包提供的 npm')
                npm_command=[node,str(npm_entry)]
            subprocess.run([*npm_command,'install','--prefix',str(tools),'--ignore-scripts','--no-audit','--no-fund','--save-exact','@larksuite/cli@'+CLI_VERSION],check=True,shell=False)
            subprocess.run([node,str(tools/'node_modules/@larksuite/cli/scripts/install.js')],check=True)
        def update(c):
            f=c['feishu'];f['cli_command']=[str(entry)];f.setdefault('cli_config_dir','.state/feishu-cli')
            f.setdefault('cli_profile','anysolvers-next')
            dsh=ROOT/'node_modules/@deepseek-ai/dsh/lib/bin.js'
            if dsh.exists():
                for k in ('router_agent','worker_host'):
                    if c[k].get('dsh_command')==['dsh','--profile','headless']:
                        c[k]['dsh_command']=[node,str(dsh),'--profile','headless']
        save(path,update)
        print(json.dumps(dict(prepared=True,config=str(path),cli_version=CLI_VERSION,enabled=load(path)['feishu'].get('enabled',False)),ensure_ascii=False));return
    if args.reuse_auth_config:
        other=load(args.reuse_auth_config)['feishu']
        # Only configuration references are shared; credential files are never opened.
        def reuse(c):
            for key in ('cli_command','cli_config_dir','cli_profile','app_id','bot_id'):
                if key in other:c['feishu'][key]=other[key]
            c['feishu']['enabled']=False
        save(path,reuse)
    cfg=load(path);f=cfg['feishu'];adapter=Feishu(None,f)
    if args.action=='disable':
        save(path,lambda c:c['feishu'].update(enabled=False));print('接入已禁用；重启服务生效。');return
    if args.action in ('new-app','existing-app'):
        if args.action=='new-app' and (Path(f['cli_config_dir'])/'config.json').exists():
            raise ValueError('专用 CLI 已有配置；请先 check，避免重复创建机器人。')
        command=['config','init','--name',f.get('cli_profile','anysolvers-next')]
        if args.action=='new-app':command+=['--new','--brand','feishu','--lang','zh']
        # Interactive official program owns stdin and credential storage. No capture/logging.
        subprocess.run(adapter.command(command),env=adapter.environment(setup=True),check=True)
        return
    identity=check(adapter,f)
    if args.action=='create-chat':
        if not args.request_id or not args.user_id or not all(re.fullmatch(r'ou_[A-Za-z0-9_-]+',v) for v in args.user_id):
            raise ValueError('create-chat requires --request-id and --user-id; group contains only the named test participants')
        from controlplane.engine import digest
        receipt=local_path(cfg,'.state/setup-chats')/(digest([identity['app_id'],args.request_id])+'.json')
        if receipt.exists():
            saved=json.loads(receipt.read_text())
            if saved.get('chat_id'):print(json.dumps(saved,ensure_ascii=False));return
            if saved.get('state')=='unknown' or time.time()-saved['attempted_at']>=9*3600:
                raise ValueError('Unknown chat creation receipt; reconcile before retrying (do not change request ID)')
        else:atomic(receipt,dict(state='creating',attempted_at=time.time(),request_id=args.request_id))
        body=dict(name=args.name,user_id_list=args.user_id,chat_mode='group',chat_type='private',description='AnySolvers deployment acceptance test group')
        # Raw API is used because +chat-create does not expose the idempotency UUID.
        result=adapter.call(['api','POST','/open-apis/im/v1/chats','--params',json.dumps(dict(user_id_type='open_id',uuid=digest([identity['app_id'],args.request_id])[:32])),'--data',json.dumps(body,ensure_ascii=False)])
        cid=result.get('chat_id')
        if not cid or not re.fullmatch(r'oc_[A-Za-z0-9_-]+',cid):raise ValueError('Missing chat creation receipt; retain request ID for reconciliation')
        atomic(receipt,dict(chat_id=cid,label=args.name,test_group=True,request_id=args.request_id))
        print(receipt.read_text());return
    if args.action=='enable':
        if not f.get('chats') or any(not c.get('allowed_users') for c in f['chats']):raise ValueError('Configure groups and allowed users first')
        for chat in f['chats']:
            adapter.call(['api','GET','/open-apis/im/v1/messages','--params',json.dumps(dict(container_id_type='chat',container_id=chat['chat_id'],page_size=1))])
        save(path,lambda c:c['feishu'].update(enabled=True,receive_mode=args.receive_mode))
        print('接入已启用；启动或重启实例生效。');return
    if args.action=='check':
        report=dict(identity=identity,enabled=f.get('enabled',False),chats=[])
        for chat in f.get('chats',[]):
            if not chat.get('chat_id'):continue
            cid=chat['chat_id']
            adapter.call(['api','GET','/open-apis/im/v1/chats/'+cid])
            value=adapter.call(['api','GET','/open-apis/im/v1/messages','--params',json.dumps(dict(container_id_type='chat',container_id=cid,page_size=1))])
            report['chats'].append(dict(chat_id=cid,history_readable=True,allowed_users=len(chat.get('allowed_users',[]))))
        def update(c):c['feishu'].update({k:identity[k] for k in ('app_id','bot_id')})
        save(path,update)
        report['live_delivery_tested']=False
        print(json.dumps(report,ensure_ascii=False,indent=2));return
    if args.action=='chats':
        result=adapter.call(['im','+chat-list','--page-all','--page-size','100'])
        print(json.dumps(result,ensure_ascii=False,indent=2));return
    if not args.chat_id or not re.fullmatch(r'oc_[A-Za-z0-9_-]+',args.chat_id):raise ValueError('需要 --chat-id oc_…')
    if args.action=='members':
        result=adapter.call(['im','+chat-members-list','--chat-id',args.chat_id,'--member-types','user','--page-all'])
        print(json.dumps(result,ensure_ascii=False,indent=2));return
    if args.action=='configure':
        if not args.user_id or not all(re.fullmatch(r'ou_[A-Za-z0-9_-]+',v) for v in [*args.user_id,*([args.notify_user] if args.notify_user else [])]):
            raise ValueError('需要至少一个 --user-id ou_…，notify-user 也必须是 open_id')
        adapter.call(['api','GET','/open-apis/im/v1/chats/'+args.chat_id])
        def update(c):
            c['feishu'].update({k:identity[k] for k in ('app_id','bot_id')})
            chats=c['feishu']['chats'];new=dict(chat_id=args.chat_id,label=args.label,allowed_users=args.user_id,notify_user=args.notify_user or '',test_group=True)
            c['feishu']['chats']=[v for v in chats if v.get('chat_id') and v['chat_id']!=args.chat_id]+[new]
        save(path,update);print('已写入指定测试群与派工者；尚未启用自动派工。');return
    # This command is explicit. Checks the real reply target belongs to the designated group.
    chat=next((c for c in f.get('chats',[]) if c['chat_id']==args.chat_id),None)
    if not chat or not args.reply_to or not re.fullmatch(r'om_[A-Za-z0-9_-]+',args.reply_to):raise ValueError('send-test 需要已配置的测试群和 --reply-to om_…')
    result=adapter.call(['api','GET','/open-apis/im/v1/messages/'+args.reply_to])
    messages=result.get('items',[])
    if len(messages)!=1 or messages[0].get('chat_id')!=args.chat_id:raise ValueError('回复目标不属于指定测试群')
    person=chat.get('notify_user') or next(iter(chat.get('allowed_users',[])),None)
    if not person or not re.fullmatch(r'ou_[A-Za-z0-9_-]+',person):raise ValueError('未配置测试 @ 对象')
    from controlplane.engine import digest
    result=adapter.call(['im','+messages-reply','--message-id',args.reply_to,'--markdown',f'<at user_id="{person}">负责人</at> AnySolvers 飞书部署测试：机器人回复与 @ 链路已接通。此消息不会启动研究。','--reply-in-thread','--idempotency-key','setup-'+digest([identity['app_id'],args.chat_id,args.reply_to])[:32]])
    print(json.dumps(dict(sent=True,result=result),ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except subprocess.CalledProcessError as exc:
        # CLI stderr/stdout may contain auth details. Do not echo captured output.
        raise SystemExit('飞书 CLI 调用失败（退出码 '+str(exc.returncode)+'）；请在自己的终端运行官方 CLI 检查应用权限。')
    except (ValueError,OSError) as exc:raise SystemExit(str(exc))
