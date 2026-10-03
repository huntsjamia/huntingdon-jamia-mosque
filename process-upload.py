"""Process the uploaded original into an unapproved draft on the processing branch."""
import base64,importlib.util,json,re,tempfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('reader','timetable-test.py');reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
job=json.loads(Path('incoming/request.json').read_text())
id=job.get('id','')
if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}',id):raise ValueError('Invalid job identity')
result={'id':id,'status':'failed','error':'The file could not be read. Check the selected month and use a clear, full timetable in the mosque layout.'}
try:
    month,ext=job['month'],job['extension']
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',month) or ext not in ['jpg','jpeg','png','webp','heic','heif','pdf']:raise ValueError('Invalid upload')
    if not re.fullmatch(r'[0-9a-f]{40}',job['baseRevision']):raise ValueError('Invalid revision')
    content=base64.b64decode(job['content'],validate=True)
    if len(content)>15*1024*1024:raise ValueError('Upload too large')
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp);source=p/('original.'+ext);source.write_bytes(content)
        reader.convert(source,p/'image.jpg');reader.extract(p/'image.jpg',month,p/'draft.json')
        data=json.loads((p/'draft.json').read_text());image=(p/'image.jpg').read_bytes()
        if len(image)>8*1024*1024:raise ValueError('Converted image too large')
        result={'id':id,'month':month,'baseRevision':job['baseRevision'],'status':'ready','reviewed':False,'rows':data['rows'],'image':base64.b64encode(image).decode()}
except Exception as exc:
    print('Reading failed:',type(exc).__name__)
Path('results').mkdir(exist_ok=True)
Path('results',id+'.json').write_text(json.dumps(result),encoding='utf-8')
print('Draft status:',result['status'])
