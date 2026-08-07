"""Deterministic symbol locations used only as workspace navigation."""
from __future__ import annotations

import ast
import re

RE_DEF_JS = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s*(?:export\s+)?class\s+(\w+)|^\s*const\s+(\w+)\s*=\s*(?:async\s*)?\(")


def extract_python_definitions(lines):
    try: tree=ast.parse("\n".join(lines))
    except (SyntaxError, ValueError, TypeError): return []
    definitions=[]
    def visit(body,prefix=""):
        for node in body:
            if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)): continue
            name=f"{prefix}.{node.name}" if prefix else node.name
            decorators=getattr(node,"decorator_list",None) or []
            start=min([node.lineno]+[d.lineno for d in decorators]); end=getattr(node,"end_lineno",None) or node.lineno
            definitions.append({"nome":name,"linha_inicio":start,"linha_fim":end,"tipo":"classe" if isinstance(node,ast.ClassDef) else "funcao_assincrona" if isinstance(node,ast.AsyncFunctionDef) else "funcao"})
            if isinstance(node,ast.ClassDef): visit(node.body,name)
    visit(tree.body); definitions.sort(key=lambda d:(d["linha_inicio"],d["linha_fim"],d["nome"])); return definitions


def extract_symbols(lines, extension):
    if extension == ".py": return [(d["nome"],d["linha_inicio"]) for d in extract_python_definitions(lines)]
    result=[]
    if extension in (".js",".ts",".jsx",".tsx"):
        for i,line in enumerate(lines,1):
            match=RE_DEF_JS.match(line)
            if match: result.append((next(group for group in match.groups() if group),i))
    return result
