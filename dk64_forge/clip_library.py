"""Personal clip labels/associations, separate from recovered ROM ownership."""
import json
from pathlib import Path
from .workspace_state import write_json

class ClipLibrary:
    def __init__(self,path):
        self.path=Path(path);self.entries={}
        try:
            if self.path.stat().st_size>2_000_000:raise ValueError("Clip library is too large")
            data=json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data,dict) or data.get("version")!=1:raise ValueError("Unsupported clip library version")
            self.entries={str(k):v for k,v in data.get("entries",{}).items() if isinstance(v,dict)}
        except (OSError,ValueError,TypeError,AttributeError):pass
    def key(self,model,clip):return f"{model}:{clip}"
    def get(self,model,clip):return self.entries.get(self.key(model,clip),{})
    def set(self,model,clip,**values):
        item=dict(self.get(model,clip));item.update(values)
        item["name"]=str(item.get("name",""))[:120]
        updated=dict(self.entries);updated[self.key(model,clip)]=item
        write_json(self.path,{"version":1,"entries":updated})
        self.entries=updated
