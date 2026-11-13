"""Offline efficiency measurement; synthetic messages, no external API/UI."""
import json,sys,time,statistics,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw,ImageFont
from core.screen_ocr import read
from core.jev_shadow import Shadow
class Judge:
 def __init__(self): self.calls=0
 def classify(self,*args): self.calls+=1;return {'choice':'defect','confidence':.9}
def summary(times):
 return {'median_ms':round(statistics.median(times)*1000,3),'max_ms':round(max(times)*1000,3)}
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);j=Judge();s=Shadow(p/'db',j)
 lines=['박성수','40차 장미 2단'];s.observe('test',lines)
 timings=[]
 for i in range(1000):
  t=time.perf_counter();s.observe('test',lines);timings.append(time.perf_counter()-t)
 repeat=summary(timings);repeat['calls']=j.calls
 changes=[]
 for i in range(20):
  lines=lines+['추가 문장 '+str(i)]
  t=time.perf_counter();s.observe('test',lines);changes.append(time.perf_counter()-t)
 s.db.close()
 font=ImageFont.truetype('C:/Windows/Fonts/malgun.ttf',28)
 expected=['박성수','40차 장미 2단','꽃길','확인했습니다']
 im=Image.new('RGB',(800,400),'white');d=ImageDraw.Draw(im)
 for i,line in enumerate(expected): d.text((20,20+i*55),line,font=font,fill='black')
 image=p/'synthetic.png';im.save(image)
 ocr=[];texts=[]
 for _ in range(10):
  t=time.perf_counter();texts.append(read(image));ocr.append(time.perf_counter()-t)
 result={'unchanged_1000':repeat,'changed_20':dict(summary(changes),mock_calls=j.calls),'ocr_10':dict(summary(ocr),exact_runs=sum(x==expected for x in texts),first_result=texts[0]),'real_jev_measured':False,'real_chat_accuracy_measured':False}
 out=Path('data/jev_shadow_benchmark.json');out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
