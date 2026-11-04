"""Exercise GPU kernels before accepting an optional replacement runtime."""
import json, sys, urllib.request
for model in ["irodori-500m-v3-q8", "irodori-500m-v3-q8-voice-stable-speaker"]:
    data=json.dumps({"model":model,"input":"動作確認です。","response_format":"wav","options":{"no_ref":True}}).encode("utf8")
    with urllib.request.urlopen(urllib.request.Request(sys.argv[1]+"/v1/audio/speech",data=data,headers={"Content-Type":"application/json"}),timeout=120) as r:
        wav=r.read()
    if wav[:4]!=b"RIFF" or len(wav)<=44:raise ValueError("Invalid WAV")
print("Both model routes generated WAV; algorithm effectiveness needs separate validation")
