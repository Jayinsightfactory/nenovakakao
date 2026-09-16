"""Register the local host; Chrome unpacked extension still needs loading."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import winreg

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT/'browser-extension'/'manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    extension_id = ''.join(chr(ord('a')+int(c,16)) for c in digest)
    target = ROOT/'data'/'browser_session_bridge'
    target.mkdir(parents=True,exist_ok=True)
    launcher = target/'host.cmd'
    launcher.write_text('@echo off\r\n"'+sys.executable+'" "'+str(ROOT/'scripts'/'nenova_session_host.py')+'" %*\r\n',encoding='utf-8')
    native = target/'native-host.json'
    native.write_text(json.dumps({'name':'com.nenovakakao.sales_session',
        'description':'Approved Nenova defect sales-input only', 'path':str(launcher),
        'type':'stdio','allowed_origins':['chrome-extension://'+extension_id+'/']},indent=2),encoding='utf-8')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Google\Chrome\NativeMessagingHosts\com.nenovakakao.sales_session') as key:
        winreg.SetValueEx(key,'',0,winreg.REG_SZ,str(native))
    print('Native host registered. Load unpacked extension:',ROOT/'browser-extension')
    print('Expected extension ID:',extension_id)


if __name__=='__main__':main()
