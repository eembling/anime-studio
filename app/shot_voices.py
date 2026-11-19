"""Explicit character-to-dialogue binding and generation from the shot editor."""
import hashlib
import json
import uuid


def edit_token(studio, shot):
    rows = studio.shot_lines(shot)
    value = [shot['dialogue'], shot['audio_mode'], [(l['line_id'], l['text'], l['speaker'], l['character_id'], l.get('direction','')) for l in rows]]
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def save_and_generate(api, body):
    st = api.st
    with st.production.lock:
        shot = st.db.one('SELECT * FROM shots WHERE id=?',(body['shot_id'],))
        if not shot: raise ValueError('Shot not found')
        if body.get('token') != edit_token(st,shot):
            raise ValueError('Shot dialogue or character choices changed. Reload this shot’s voice editor before saving.')
        if shot['audio_mode'] == 'file' and not body.get('replace_file'):
            raise ValueError('This shot uses a whole-shot audio file. Confirm switching to character dialogue voices first.')
        incoming = body.get('lines')
        if not isinstance(incoming,list) or len(incoming)>200:
            raise ValueError('Provide at most 200 dialogue lines')
        old = {l['line_id']:l for l in st.shot_lines(shot)}
        chars = {a['id']:a for a in st.db.q("SELECT * FROM assets WHERE kind='character'")}
        prepared=[];seen=set()
        for row in incoming:
            ident=row.get('line_id') or uuid.uuid4().hex
            if ident in seen or (row.get('line_id') and ident not in old):
                raise ValueError('Invalid or duplicate dialogue line identity')
            seen.add(ident)
            text=' '.join(str(row.get('text') or '').split())
            if not text: raise ValueError('Enter spoken words for each line, or remove the empty line.')
            if len(text)>20000: raise ValueError('Dialogue line exceeds 20,000 characters')
            char_id=int(row.get('character_id') or 0) or None
            if char_id and char_id not in chars: raise ValueError('Choose an existing character, not a location.')
            character=chars.get(char_id)
            speaker=character['name'] if character else (old.get(ident) or {}).get('speaker') or 'Unassigned'
            from script_dialogue import dialogue_line
            label=dialogue_line(speaker+': spoken words')
            if not label or label['speaker']!=speaker:
                speaker='Character '+str(char_id) if char_id else 'Unassigned'
            previous=old.get(ident,{})
            same=(previous.get('text')==text and previous.get('character_id')==char_id and previous.get('direction','')==str(row.get('direction') or '').strip())
            prepared.append(dict(speaker=speaker,text=text,character_id=char_id,line_id=ident,
                                 direction=str(row.get('direction') or '').strip()[:2000],
                                 audio=previous.get('audio') if same else None,
                                 src=previous.get('src') if same else None,clip_id=previous.get('clip_id') if same else None))
        requested=body.get('generate',[])
        if not isinstance(requested,list) or any(i not in range(len(prepared)) for i in requested):
            raise ValueError('Choose valid dialogue rows to generate')
        requested=list(dict.fromkeys(requested))
        # Bind validated words/characters first; generation then consumes these exact saved lines.
        st.editor.checkpoint('Before shot dialogue and character voices')
        dialogue='\n'.join((l['speaker']+': ' if l['speaker'] else '')+l['text'] for l in prepared)
        cast=json.loads(shot['asset_ids'] or '[]')
        for line in prepared:
            if line['character_id'] and line['character_id'] not in cast:cast.append(line['character_id'])
        st.db.x("UPDATE shots SET dialogue=?,asset_ids=?,audio_mode='lines',audio=CASE WHEN audio_mode='file' THEN NULL ELSE audio END WHERE id=?",(dialogue,json.dumps(cast),shot['id']))
        st.save_lines(shot['id'],prepared)
        try:st.rebuild_audio(shot['id'],fit=False)
        except Exception as error:
            return {'ok':False,'dialogue_saved':True,'saved':len(prepared),'queued':0,'error':'Dialogue saved, but rebuilding shot audio failed: '+str(error)}
        if not requested:return {'ok':True,'saved':len(prepared),'queued':0,'skipped':[]}
        result=api.generate({'stage':'voice','lines':[{'shot_id':shot['id'],'index':i} for i in requested],
                             'takes':body.get('takes',1),'select_generated':True})
        # Saving is successful even when readiness prevents generation; surface this distinctly.
        return dict(result,saved=len(prepared),dialogue_saved=True)
