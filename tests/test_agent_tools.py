import hashlib
import eyle.core.tools as tools

FIELDS={"status","ok","executed","changed","error_code","detail"}
HASH='a'*64

def ctx(root):
    return {'projeto':{'caminho_origem':str(root)},'config':{'agent':{'max_read_range_lines':400,'max_tree_entries':200,'max_tree_depth':6},'codar':{'testes':{'ativado':False}}}}

def test_live_read_tools_share_envelope(tmp_path):
    (tmp_path/'a.py').write_text('def x():\n    return 1\n')
    calls=[('list_tree',{}),('search_code',{'query':'return 1'}),('find_symbol',{'simbolo':'x'}),('read_file',{'caminho_relativo':'a.py'}),('read_range',{'caminho_relativo':'a.py','linha_inicio':1,'linha_fim':2})]
    for name,args in calls:
        result=tools.executar_tool(name,args,ctx(tmp_path))
        assert set(result)==FIELDS and result['ok'] is True, (name,result)

def test_search_code_reads_live_workspace_without_index(tmp_path):
    content='volume = 1\nvolume += 1\n'; (tmp_path/'audio.py').write_text(content)
    result=tools.executar_tool('search_code',{'query':'volume'},ctx(tmp_path))
    assert result['ok'] is True
    item=result['detail']['resultados'][0]
    assert 'volume = 1' in item['trecho_numerado']
    assert item['file_hash']==hashlib.sha256(content.encode()).hexdigest()

def test_read_metadata_is_not_in_core_catalog():
    assert 'read_metadata' not in tools.TOOLS
    assert 'read_metadata' not in [x['name'] for x in tools.gerar_catalogo_tools()]

def test_permissions_and_schemas_are_explicit():
    assert tools.TOOLS['run_tests']['permission']=='EXEC'
    assert all(item['permission'] != 'WRITE' for item in tools.TOOLS.values())
    for name,item in tools.TOOLS.items():
        assert item['input_schema']['type']=='object'
        assert item['input_schema']['additionalProperties'] is False
        assert item['description'] and item['output_schema']

def test_validation_rejects_unknown_and_bad_arguments(tmp_path):
    bad=tools.executar_tool('read_range',{'caminho_relativo':'a.py','linha_inicio':'1','linha_fim':2},ctx(tmp_path))
    assert bad['error_code']=='INVALID_ARGUMENT' and bad['executed'] is False
    missing=tools.executar_tool('read_range',{'caminho_relativo':'a.py','linha_inicio':1},ctx(tmp_path))
    assert missing['error_code']=='INVALID_ARGUMENT'

def test_run_tests_skipped_is_not_claimed_as_executed(tmp_path,monkeypatch):
    monkeypatch.setattr(tools,'rodar_testes_projeto',lambda *a,**k:{'executado':False,'ok':True,'detalhe':'sem suite'})
    result=tools.executar_tool('run_tests',{},ctx(tmp_path))
    assert result=={'status':'skipped','ok':True,'executed':False,'changed':False,'error_code':'TESTS_DISABLED','detail':"A execução de testes está desativada em config['codar']['testes']['ativado']."}

def test_patch_operations_are_not_public_tools():
    names=set(tools.TOOLS)
    assert not {"apply_patch","test_patch_dry_run","apply_patch_set","test_patch_set_dry_run"} & names
