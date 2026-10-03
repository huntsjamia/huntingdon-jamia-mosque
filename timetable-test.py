import calendar, csv, io, re
NAMES = ['Fajr','Zuhr','Asr','Maghrib','Isha']
def validate(data, month):
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', month): raise ValueError('Invalid month')
    count = calendar.monthrange(*map(int,month.split('-')))[1]
    if data.get('month') != month: raise ValueError('Picture month does not match selected month')
    rows = data.get('rows')
    if not isinstance(rows,list) or len(rows) != count: raise ValueError('Missing daily rows')
    for day,row in enumerate(rows,1):
        if row.get('day') != day: raise ValueError('Days missing, duplicated or out of order')
        times=row.get('times')
        if not isinstance(times,list) or len(times)!=5: raise ValueError('Five prayer columns required')
        for value in times:
            if value is not None and (not isinstance(value,str) or not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',value)):
                raise ValueError('Invalid prayer time')
    return rows

def make_csv(data,month):
    rows=validate(data,month)
    if any(t is None for r in rows for t in r['times']): raise ValueError('Resolve unreadable times before publishing')
    out=io.StringIO(newline='');writer=csv.writer(out);writer.writerow(['Date',*NAMES])
    writer.writerows([[r['day'],*r['times']] for r in rows]);return out.getvalue()

"""Convert an original upload into a review image; never modify the source."""
import sys, warnings
from pathlib import Path
from PIL import Image, ImageOps
Image.MAX_IMAGE_PIXELS = 40_000_000
warnings.simplefilter('error', Image.DecompressionBombWarning)
def convert(source, target):
    source, target = Path(source), Path(target)
    if source.stat().st_size > 15 * 1024 * 1024:
        raise ValueError('Choose a file smaller than 15 MB')
    if source.suffix.lower() == '.pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(source) as pdf:
            if len(pdf) != 1:
                raise ValueError('Please upload a single-page monthly timetable PDF')
            page = pdf[0]
            width, height = page.get_size()
            if width * height * 9 > Image.MAX_IMAGE_PIXELS:
                raise ValueError('PDF page is too large')
            im = page.render(scale=3).to_pil().copy()
    else:
        if source.suffix.lower() in ('.heic', '.heif'):
            from pillow_heif import register_heif_opener
            register_heif_opener()
        with Image.open(source) as original:
            if getattr(original, 'n_frames', 1) != 1:
                raise ValueError('Please upload a single image')
            im = ImageOps.exif_transpose(original).convert('RGBA')
    background = Image.new('RGB', im.size, 'white')
    if im.mode == 'RGBA': background.paste(im, mask=im.getchannel('A'))
    else: background.paste(im.convert('RGB'))
    target.parent.mkdir(parents=True, exist_ok=True)
    background.save(target, 'JPEG', quality=95)

"""Offline OCR: no API key, no network calls, no automatic publication."""
import calendar,json,re,sys
from collections import Counter
from pathlib import Path
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR


def groups(values):
    out=[]
    for v in values:
        if not out or v>out[-1][-1]+2:out.append([int(v)])
        else:out[-1].append(int(v))
    return out

def grid(im,count):
    a=np.array(im.convert('RGB'));h,w=a.shape[:2];dark=np.all(a<110,axis=2)
    hits=np.where(dark[:,round(w*.1):round(w*.92)].sum(axis=1)>w*.55)[0]
    hits=hits[(hits>h*.25)&(hits<h*.95)]
    ys=[(g[-1] if g[0]<h*.5 else g[0]) if g[-1]-g[0]>5 else round(np.mean(g)) for g in groups(hits)]
    candidates=[]
    for i in range(len(ys)-count):
        band=ys[i:i+count+1];gaps=np.diff(band);avg=np.mean(gaps);score=np.ptp(gaps)
        if h*.008<avg<h*.04 and score<avg*.45:candidates.append((score,band))
    if not candidates:raise ValueError('Could not locate all daily rows. Use the full clear timetable.')
    ys=min(candidates,key=lambda x:x[0])[1]
    hits=np.where(dark[ys[0]:ys[-1]].sum(axis=0)>(ys[-1]-ys[0])*.7)[0]
    xs=[round(np.mean(g)) for g in groups(hits[(hits>w*.075)&(hits<w*.95)])]
    if len(xs)!=12:raise ValueError('Expected the mosque 11-column layout')
    return xs,ys

def normalise(text):
    s=re.sub(r'\s','',text).strip('.:')
    if re.fullmatch(r'\d{3,4}',s):s=s[:-2]+':'+s[-2:]
    m=re.fullmatch(r'(\d{1,2})[.:](\d{2})',s)
    return f'{int(m[1]):02}:{m[2]}' if m and int(m[1])<24 and int(m[2])<60 else None

def extract(image,month,output):
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',month):raise ValueError('Invalid month')
    count=calendar.monthrange(*map(int,month.split('-')))[1]
    im=Image.open(image).convert('RGB');xs,ys=grid(im,count)
    engine=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=2)
    rows=[];readings=[]
    for day in range(count):
        times=[]
        for col in [2,5,7,8,10]:
            value=None;attempts=[]
            for inset in [2,3,1]:
                cell=im.crop((xs[col]+2,ys[day]+inset,xs[col+1]-2,ys[day+1]-inset))
                cell=cell.resize((cell.width*3,cell.height*3))
                result,_=engine(np.array(cell)[:,:,::-1],use_det=False,use_cls=False)
                text,confidence=result[0] if result else ('',0)
                attempts.append({'text':text,'confidence':float(confidence)})
                value=normalise(text) if confidence>=.8 else None
            votes=Counter(normalise(a['text']) for a in attempts if a['confidence']>=.9 and normalise(a['text']))
            value=next(iter(votes)) if len(votes)==1 else None
            times.append(value);readings.append({'day':day+1,'column':col,'attempts':attempts})
        rows.append({'day':day+1,'times':times})
    data={'month':month,'rows':rows,'reviewed':False,'readings':readings,'monthNeedsReview':True}
    validate(data,month);Path(output).write_text(json.dumps(data,indent=2),encoding='utf-8')
    print(f'Read {sum(t is not None for r in rows for t in r["times"])} of {count*5} times')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('source');parser.add_argument('month');parser.add_argument('output');parser.add_argument('--reference')
    args=parser.parse_args();folder=Path(args.output);folder.mkdir(parents=True,exist_ok=True)
    convert(args.source,folder/'monthly-timetable.jpg')
    extract(folder/'monthly-timetable.jpg',args.month,folder/'timetable.json')
    data=json.loads((folder/'timetable.json').read_text())
    if args.reference:
        reference=list(csv.reader(open(args.reference,encoding='utf-8-sig')))[1:]
        differences=[{'day':r['day'],'column':i,'read':a,'expected':normalise(b)} for r,line in zip(data['rows'],reference) for i,(a,b) in enumerate(zip(r['times'],line[1:])) if a!=normalise(b)]
        wrong=[d for d in differences if d['read'] is not None]
        (folder/'accuracy.json').write_text(json.dumps({'values':len(data['rows'])*5,'differences':differences},indent=2))
        print(json.dumps({'values':len(data['rows'])*5,'unreadable':len(differences)-len(wrong),'wrong':len(wrong)}))
        if len(reference)!=len(data['rows']) or wrong:raise SystemExit('Accuracy regression')
