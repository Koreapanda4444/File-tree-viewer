from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import json

from models import FileMeta
from utils import now_ts, human_type_from_name

@dataclass
class VMNode:
    id: str
    name: str
    is_dir: bool
    parent_id: Optional[str] = None
    children: List[str] = field(default_factory=list)
    content: str = ""
    created_ts: float = field(default_factory=now_ts)
    updated_ts: float = field(default_factory=now_ts)

class VirtualFSBackend:
    def __init__(self):
        self.nodes: Dict[str, VMNode] = {}
        self.root_id: str = "root"
        self._id_counter = 0
        self.show_hidden: bool = True
        self.reset()

    def reset(self):
        self.nodes = {}
        self._id_counter = 0
        self.nodes[self.root_id] = VMNode(id=self.root_id, name="VM:/", is_dir=True, parent_id=None)
        docs = self.make_folder(self.root_id, "Documents")
        proj = self.make_folder(docs, "Projects")
        app2 = self.make_folder(proj, "App2")
        self.make_file(app2, "config.json", "{\n  \"hello\": \"vm\"\n}\n")
        self.make_file(app2, "main.py", "print('Hello from VM')\n")

    def _new_id(self) -> str:
        self._id_counter += 1
        return f"n{self._id_counter}"

    def list_children(self, node_id: str) -> List[Tuple[str, bool]]:
        node = self.nodes[node_id]
        items = [(cid, self.nodes[cid].is_dir) for cid in node.children]
        items.sort(key=lambda x: (not x[1], self.nodes[x[0]].name.lower()))
        return items

    def get_path(self, node_id: str) -> str:
        parts = []
        cur = node_id
        while cur and cur != self.root_id:
            n = self.nodes[cur]
            parts.append(n.name)
            cur = n.parent_id
        parts.reverse()
        return "VM:/" + "/".join(parts)

    def get_meta(self, node_id: str) -> FileMeta:
        n = self.nodes[node_id]
        size = len(n.content.encode("utf-8")) if not n.is_dir else None
        return FileMeta(
            name=n.name,
            path=self.get_path(node_id),
            type=human_type_from_name(n.name, n.is_dir),
            size_bytes=size,
            modified_ts=n.updated_ts,
        )

    def make_folder(self, parent_id: str, name: str) -> str:
        parent = self.nodes[parent_id]
        if not parent.is_dir:
            raise ValueError("Parent is not a folder.")
        nid = self._new_id()
        self.nodes[nid] = VMNode(id=nid, name=name, is_dir=True, parent_id=parent_id)
        parent.children.append(nid)
        parent.updated_ts = now_ts()
        return nid

    def make_file(self, parent_id: str, name: str, content: str = "") -> str:
        parent = self.nodes[parent_id]
        if not parent.is_dir:
            raise ValueError("Parent is not a folder.")
        nid = self._new_id()
        self.nodes[nid] = VMNode(id=nid, name=name, is_dir=False, parent_id=parent_id, content=content)
        parent.children.append(nid)
        parent.updated_ts = now_ts()
        return nid

    def rename(self, node_id: str, new_name: str) -> str:
        n = self.nodes[node_id]
        n.name = new_name
        n.updated_ts = now_ts()
        return node_id

    def delete(self, node_id: str) -> None:
        if node_id == self.root_id:
            raise ValueError("Cannot delete VM root.")
        n = self.nodes[node_id]
        if n.parent_id:
            parent = self.nodes[n.parent_id]
            parent.children = [cid for cid in parent.children if cid != node_id]
            parent.updated_ts = now_ts()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            cn = self.nodes[cur]
            stack.extend(list(cn.children))
            del self.nodes[cur]

    def move(self, node_id: str, dst_parent_id: str) -> str:
        if node_id == self.root_id:
            raise ValueError("Cannot move VM root.")
        n = self.nodes[node_id]
        dst = self.nodes[dst_parent_id]
        if not dst.is_dir:
            raise ValueError("Destination must be a folder.")
        # prevent moving into itself/descendants
        cur = dst_parent_id
        while cur:
            if cur == node_id:
                raise ValueError("Cannot move a folder into itself/descendant.")
            cur = self.nodes[cur].parent_id
        if n.parent_id:
            p = self.nodes[n.parent_id]
            p.children = [cid for cid in p.children if cid != node_id]
            p.updated_ts = now_ts()
        n.parent_id = dst_parent_id
        dst.children.append(node_id)
        dst.updated_ts = now_ts()
        return node_id

    def copy(self, node_id: str, dst_parent_id: str) -> str:
        dst = self.nodes[dst_parent_id]
        if not dst.is_dir:
            raise ValueError("Destination must be a folder.")

        def clone(src_id: str, new_parent: str) -> str:
            src = self.nodes[src_id]
            nid = self._new_id()
            nn = VMNode(
                id=nid,
                name=src.name,
                is_dir=src.is_dir,
                parent_id=new_parent,
                content=src.content,
                created_ts=now_ts(),
                updated_ts=now_ts(),
            )
            self.nodes[nid] = nn
            self.nodes[new_parent].children.append(nid)
            if src.is_dir:
                for cid in list(src.children):
                    clone(cid, nid)
            return nid

        return clone(node_id, dst_parent_id)

    def to_json(self) -> str:
        data = {
            "root_id": self.root_id,
            "id_counter": self._id_counter,
            "nodes": {
                nid: {
                    "id": n.id,
                    "name": n.name,
                    "is_dir": n.is_dir,
                    "parent_id": n.parent_id,
                    "children": list(n.children),
                    "content": n.content,
                    "created_ts": n.created_ts,
                    "updated_ts": n.updated_ts,
                }
                for nid, n in self.nodes.items()
            },
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def load_json(self, s: str) -> None:
        data = json.loads(s)
        self.root_id = data.get("root_id", "root")
        self._id_counter = int(data.get("id_counter", 0))
        self.nodes = {}
        for nid, nd in data["nodes"].items():
            self.nodes[nid] = VMNode(
                id=nd["id"],
                name=nd["name"],
                is_dir=nd["is_dir"],
                parent_id=nd.get("parent_id"),
                children=list(nd.get("children", [])),
                content=nd.get("content", ""),
                created_ts=float(nd.get("created_ts", now_ts())),
                updated_ts=float(nd.get("updated_ts", now_ts())),
            )
        if self.root_id not in self.nodes:
            self.nodes[self.root_id] = VMNode(id=self.root_id, name="VM:/", is_dir=True)
