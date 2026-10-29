"use strict";
const assert = require('node:assert/strict');
for (const browser of ['chrome', 'firefox']) {
  const P = require(`../${browser}-extension/text-processor.js`);
  const V = require(`../${browser}-extension/voice-readiness.js`);
  const voices = [{id:'kurisu',name:'クリス'},{id:'yui',name:'由比ヶ浜'},{id:'nare',name:'地の文'},{id:'runa',name:'タグなし'},{id:'default',name:'既定'}];
  const base = P.mergedSettings({cachedVoices:voices, defaultVoice:'default', narratorVoice:'nare', dialogueVoice:'runa', narrationEnabled:true,characterTagPriority:true,removeMarkdown:false,dialoguePairs:['「」','（）'],characterMappings:[{enabled:true,tag:'助手',voice:'kurisu',manual:true}]});
  function check(label,input,expected,settings=base) {
    const result=P.processText(input,settings);
    assert.deepEqual(result.map(x=>[x.text,x.voice]),expected,`${browser} ${label}`);
    for(const segment of result) {
      const resolved=V.resolveJobVoice({...segment,voiceRoute:{...segment.voiceRoute,locked:true}},settings,voices,voices,settings.defaultVoice);
      assert.equal(resolved.ok,true,label); assert.equal(resolved.voiceId,segment.voice,label);
    }
  }
  const scenarios = [
    ['A','とここで彼女が言った「今日はいい天気だね」',[['とここで彼女が言った','nare'],['今日はいい天気だね','runa']]],
    ['B','【クリス】「明日は曇りらしい」（嫌だなあ）そう思いながら過ごしていると「明日台風らしいよ」と誰かが言った',[['明日は曇りらしい','kurisu'],['嫌だなあ','kurisu'],['そう思いながら過ごしていると','nare'],['明日台風らしいよ','runa'],['と誰かが言った','nare']]],
    ['C','【クリス】「こんにちは」「元気？」（今日は楽しいな）',[['こんにちは','kurisu'],['元気？','kurisu'],['今日は楽しいな','kurisu']]],
    ['D','【クリス】「こんにちは」彼女は笑った。【由比ヶ浜】「やっはろー！」',[['こんにちは','kurisu'],['彼女は笑った。','nare'],['やっはろー！','yui']]],
    ['E','【クリス】「こんにちは」彼女は笑った。「また明日ね」',[['こんにちは','kurisu'],['彼女は笑った。','nare'],['また明日ね','runa']]]
  ];
  for(const [label,input,expected] of scenarios) {
    check(label,input,expected);
    check(`${label} priority OFF`,input,expected.map(([t,v])=>[t,v==='kurisu'||v==='yui'?'runa':v]),{...base,characterTagPriority:false});
  }
  for(const [open,close] of [['【','】'],['[',']'],['＜','＞'],['{','}'],['<<','>>']]) {
    for(const [left,right] of [['「','」'],['（','）'],['(',')'],['『','』'],['〈','〉']]) {
      for(const tag of ['クリス','助手','kurisu']) {
        const s={...base,speakerTagOpen:open,speakerTagClose:close,dialoguePairs:[left+right]};
        const marker=open+tag+close;
        const body=left+'一'+right+' \n '+left+'二'+right+'地の文'+left+'三'+right;
        check('custom scope',marker+body,[['一','kurisu'],['二','kurisu'],['地の文','nare'],['三','runa']],s);
        check('custom priority OFF',marker+body,[['一','runa'],['二','runa'],['地の文','nare'],['三','runa']],{...s,characterTagPriority:false});
        for(const priority of [true,false]) check('narration OFF unchanged',marker+body,[[body,priority?'kurisu':'default']],{...s,narrationEnabled:false,characterTagPriority:priority});
      }
    }
  }
  check('leading prose ends tag','【クリス】彼女は言った「こんにちは」',[['彼女は言った','nare'],['こんにちは','runa']]);
  check('full width only','【クリス】（一）(地の文)（二）',[['一','kurisu'],['(地の文)','nare'],['二','runa']],{...base,dialoguePairs:['（）']});
  check('half width only','【クリス】(一)（地の文）(二)',[['一','kurisu'],['（地の文）','nare'],['二','runa']],{...base,dialoguePairs:['()']});
  check('explicit narration','【ナレーション】「一」地の文「二」',[['一','nare'],['地の文','nare'],['二','nare']]);
  check('unknown keep','【未登録】「一」',[['【未登録】','nare'],['一','runa']]);
  check('unknown skip','【未登録】「一」',[],{...base,unknownTagBehavior:'skip'});
  check('unknown read','【未登録】「一」',[['未登録。','nare'],['一','runa']],{...base,unknownTagBehavior:'read'});
  console.log(`${browser}: A–E Voice IDs, 4 toggle combinations, 25 delimiter pairs, aliases/IDs, whitespace, unknown tags: passed`);
}
