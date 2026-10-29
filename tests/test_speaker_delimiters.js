"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
for (const browser of ['chrome', 'firefox']) {
  const dir = `../${browser}-extension/`;
  const P = require(dir + 'text-processor.js');
  const base = P.mergedSettings({cachedVoices: [{id:'kurisu',name:'クリス'}], defaultVoice:'default', narratorVoice:'narrator',dialogueVoice:'dialogue',removeMarkdown:false,dialoguePairs:['「」','（）'],characterMappings:[{tag:'助手',voice:'kurisu',manual:true}]});
  const routes = (text,s) => P.processText(text,s).map(x=>[x.text,x.voice]);
  for (const [open,close] of [['【','】'],['[',']'],['＜','＞'],['{','}'],['<<','>>'],['.*','+?'],['🎤','🔚']]) {
    for (const tag of ['クリス','助手','kurisu']) {
      for (const narrationEnabled of [true,false]) for (const characterTagPriority of [true,false]) {
        const s=P.mergedSettings({...base,speakerTagOpen:open,speakerTagClose:close,narrationEnabled,characterTagPriority});
        const body='（明日どこに行こうかなー）（いやいや明日仕事あるからね！）そう言って帰った。';
        assert.deepEqual(routes(`${open}${tag}${close}${body}`,s),narrationEnabled ? [
          ['明日どこに行こうかなー',characterTagPriority?'kurisu':'dialogue'],['いやいや明日仕事あるからね！',characterTagPriority?'kurisu':'dialogue'],['そう言って帰った。','narrator']
        ]:[[body,characterTagPriority?'kurisu':'default']]);
      }
    }
  }
  const s=P.mergedSettings({...base,speakerTagOpen:'[',speakerTagClose:']',narrationEnabled:true});
  assert.deepEqual(routes('[クリス]「一」（二）地の文',s),[['一','kurisu'],['二','kurisu'],['地の文','narrator']]);
  assert.deepEqual(routes('【クリス】「一」',s),[['【クリス】','narrator'],['一','dialogue']]);
  for (const value of ['',null,'\n']) {
    const fallback=P.mergedSettings({...base,speakerTagOpen:value,speakerTagClose:']',narrationEnabled:true});
    assert.equal(fallback.speakerTagOpen,'【');assert.equal(fallback.speakerTagClose,'】');
    assert.deepEqual(routes('【クリス】「一」',fallback),[['一','kurisu']]);
  }
  const remote=P.mergeSettingsPatch(base,{speakerTagOpen:'[',speakerTagClose:']'},base);
  const draft=P.mergedSettings({...base,dialoguePairs:['（）']});
  const merged=P.reconcileSettingsEditor(remote,draft,base).settings;
  assert.equal(merged.speakerTagOpen,'[');assert.equal(merged.speakerTagClose,']');
  assert.deepEqual(merged.dialoguePairs,['（）']);assert.deepEqual(merged.characterMappings,base.characterMappings);
  assert.equal(P.settingsExport(s).speakerTagOpen,'[');
  for (const page of ['options','sidebar']) {
    const html=fs.readFileSync(require.resolve(dir+page+'.html'),'utf8');
    for(const id of ['speakerTagOpen','speakerTagClose']) assert.equal(html.split(`id="${id}"`).length,2);
  }
  console.log(`${browser}: delimiter/routing matrix 84 cases + compatibility, merge, export, UI fields passed`);
}
