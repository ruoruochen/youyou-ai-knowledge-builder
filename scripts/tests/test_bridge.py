import tempfile
import unittest
import json
from unittest.mock import patch
from pathlib import Path
from bridge import Vault, CATEGORIES
import os
ROOT = Path(os.environ['KB_TEST_WORK'])

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'work')
        self.v = Vault(self.temp.name)
        for c in CATEGORIES:
            (self.v.root/c).mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def create(self):
        return self.v.run(dict(operation='create',authorization='记下来',user_instruction='把这个测试记下来',category='资产',title='测试',body='# 测试\n学习方法'))

    def change(self, op, note, **kw):
        return self.v.run(dict(operation=op,authorization=op,user_instruction='测试指令',path=note['path'],expected_revision=note['revision'],**kw))

    def test_no_authorization(self):
        with self.assertRaises(ValueError):
            self.v.run(dict(operation='create'))
        self.assertEqual(self.v.notes(), [])

    def test_crud_archive_restore_and_history(self):
        a=self.create()
        self.assertEqual(self.v.run(dict(operation='search',query='学习'))['total'],1)
        b=self.change('update',a,body='# 新内容\n新观点')
        c=self.change('archive',b)
        self.assertEqual(self.v.run(dict(operation='list'))['items'],[])
        self.assertEqual(self.v.run(dict(operation='list',include_archived=True))['total'],1)
        d=self.change('restore',c)
        e=self.change('rollback',d,version=a['revision'])
        self.assertEqual(e['body'],a['body'])

    def test_duplicate(self):
        a=self.create()
        b=self.create()
        self.assertEqual(b['status'],'duplicate')
        self.assertEqual(len(self.v.notes()),1)

    def test_external_edit_conflict(self):
        a=self.create()
        self.v.path(a['path']).write_text('外部新内容',encoding='utf-8')
        with self.assertRaises(ValueError):
            self.change('update',a,body='旧内容覆盖')
        self.assertEqual(self.v.read(a['path'])['body'],'外部新内容')

    def test_path_escape(self):
        with self.assertRaises(ValueError):
            self.v.read('../secret.md')
        (self.v.root/'资产'/'link.md').symlink_to(ROOT/'AGENTS.md')
        with self.assertRaises(ValueError):
            self.v.read('资产/link.md')

    def test_same_title_no_overwrite(self):
        self.create()
        with self.assertRaises(ValueError):
            self.v.run(dict(operation='create',authorization='记下来',user_instruction='记下来',category='资产',title='测试',body='不同内容'))

    def test_read_operations_do_not_write(self):
        a = self.create()
        def snapshot():
            return {str(p.relative_to(self.v.root)):p.read_bytes() for p in self.v.root.rglob('*') if p.is_file()}
        before = snapshot()
        self.v.run(dict(operation='list'))
        self.v.run(dict(operation='search', query='学习'))
        self.v.run(dict(operation='read', path=a['path']))
        self.v.run(dict(operation='history', path=a['path']))
        self.assertEqual(before, snapshot())

    def test_all_mutations_require_matching_authorization(self):
        a = self.create()
        for op in ('update','archive','restore','rollback'):
            with self.subTest(op=op), self.assertRaises(ValueError):
                self.v.run(dict(operation=op, authorization='记下来', user_instruction='旧授权', path=a['path'], expected_revision=a['revision']))
        self.assertEqual(a['revision'], self.v.read(a['path'])['revision'])

    def test_external_yaml_properties_survive_changes(self):
        path = '项目/手动笔记.md'
        text = '---\ntags:\n  - 阅读\n  - 思考\naliases: [别名一, 别名二]\ncustom:\n  nested: yes\n# 我的注释\ncreated: 2026-09-20\nstatus: active\n---\n# 正文\n\n手动写的内容\n'
        self.v.path(path).write_text(text, encoding='utf-8')
        a = self.v.read(path)
        b = self.change('update', a, body='# 更新正文\n新的段落')
        for key in ('tags','aliases','custom','created'):
            self.assertEqual(a['metadata'][key],b['metadata'][key])
        self.assertIn('# 我的注释',self.v.path(path).read_text(encoding='utf-8'))
        c = self.change('archive', b)
        self.assertEqual(c['metadata']['tags'],['阅读','思考'])
        self.assertEqual(c['body'],b['body'])

    def test_properties_search_and_edit(self):
        a = self.create()
        b = self.change('update', a, properties={'tags':['专注'], 'aliases':['另一个称呼'], 'title':'新标题'})
        self.assertEqual(b['body'],a['body'])
        self.assertEqual(self.v.run(dict(operation='search',query='另一个称呼'))['total'],1)
        c = self.change('update', b, properties={'tags':['行动']})
        self.assertEqual(c['metadata']['tags'],['行动'])
        with self.assertRaises(ValueError):
            self.change('update',c,properties={'status':'archived'})

    def test_history_read_and_tamper_protection(self):
        a = self.create()
        b = self.change('update',a,body='更改')
        history = self.v.run(dict(operation='history',path=a['path']))
        self.assertEqual(history[0]['version'],a['revision'])
        version = self.v.run(dict(operation='read_version',path=a['path'],version=a['revision']))
        self.assertEqual(version['body'],a['body'])
        (self.v.history_dir(a['path'])/(a['revision']+'.md')).write_text('被改过',encoding='utf-8')
        with self.assertRaises(ValueError):
            self.change('rollback',b,version=a['revision'])
        self.assertEqual(self.v.read(a['path'])['revision'],b['revision'])

    def test_reject_absolute_parent_hidden_and_symlink_paths(self):
        a = self.create()
        for name in (str(self.v.path(a['path'])), '资产/../项目/笔记.md','资产/.private.md','资产/../../secret.md',''):
            with self.subTest(path=name),self.assertRaises(ValueError):
                self.v.read(name)
        (self.v.root/'项目'/'link').symlink_to(self.v.root/'资产', target_is_directory=True)
        with self.assertRaises(ValueError):
            self.v.read('项目/link/测试.md')
        self.assertEqual(self.v.run(dict(operation='list'))['total'],1)

    def test_existing_link_survives_archive(self):
        a = self.create()
        b = self.v.run(dict(operation='create',authorization='记下来',user_instruction='记下来',category='项目',title='链接测试',body='相关：[[资产/测试]]'))
        self.change('archive',a)
        self.assertTrue(self.v.path(a['path']).exists())
        self.assertEqual(self.v.read(b['path'])['body'],b['body'])

    def test_crlf_rollback_is_byte_exact(self):
        p='灵感库/换行测试.md'
        content=b'---\r\nstatus: active\r\n---\r\nhello\r\n'
        self.v.path(p).write_bytes(content)
        a=self.v.read(p)
        b=self.change('update',a,body='changed')
        self.change('rollback',b,version=a['revision'])
        self.assertEqual(self.v.path(p).read_bytes(),content)

    def test_write_detects_late_external_edit(self):
        a=self.create()
        original=Path.write_text
        def write_and_interfere(p, text, **kw):
            result=original(p,text,**kw)
            if p.suffix=='.tmp':
                original(self.v.path(a['path']),'来自Obsidian的新内容',encoding='utf-8')
            return result
        with patch.object(Path,'write_text',write_and_interfere),self.assertRaises(ValueError):
            self.change('update',a,body='即将写入的旧内容')
        self.assertEqual(self.v.read(a['path'])['body'],'来自Obsidian的新内容')

    def test_paging_and_summary(self):
        self.create()
        rows=self.v.run(dict(operation='list',summary=True,limit=1))
        self.assertEqual(rows['total'],1)
        self.assertNotIn('body',rows['items'][0])
        self.assertEqual(self.v.run(dict(operation='list',offset=1))['items'],[])

    def test_invalid_frontmatter_never_overwritten(self):
        a=self.create()
        self.v.path(a['path']).write_text('---\nunclosed: header',encoding='utf-8')
        with self.assertRaises(ValueError):
            self.change('update',a,body='覆盖')
        self.assertEqual(self.v.path(a['path']).read_text(encoding='utf-8'),'---\nunclosed: header')

    def seed_scopes(self):
        for category in ('项目','资产','技能与模板','资源','灵感库'):
            self.v.run(dict(operation='create',authorization='记下来',user_instruction='保存隔离测试样本',category=category,title=category+'样本',body='素材相同关键词：'+category))

    def test_my_search_reads_only_my_three_categories(self):
        self.seed_scopes()
        with patch.object(self.v,'read',wraps=self.v.read) as reader:
            r=self.v.run(dict(operation='search',query='素材',scope='owned'))
            paths=[c.args[0] for c in reader.call_args_list]
        self.assertEqual(r['total'],3)
        self.assertEqual({x['category'] for x in r['items']},{'项目','资产','技能与模板'})
        self.assertTrue(all(Path(p).parts[0] in ('项目','资产','技能与模板') for p in paths))

    def test_scope_default_external_all(self):
        self.seed_scopes()
        self.assertEqual(self.v.run(dict(operation='list'))['total'],3)
        external=self.v.run(dict(operation='search',query='素材',scope='external'))
        self.assertEqual({x['category'] for x in external['items']},{'资源','灵感库'})
        self.assertEqual(self.v.run(dict(operation='list',scope='all'))['total'],5)

    def test_owned_read_cannot_follow_external_link(self):
        self.seed_scopes()
        with self.assertRaises(ValueError):
            self.v.run(dict(operation='read',path='资源/资源样本.md',scope='owned'))
        r=self.v.run(dict(operation='read',path='资源/资源样本.md',scope='external'))
        self.assertEqual(r['scope'],'external')

    def test_owned_search_does_not_parse_malformed_external_file(self):
        self.create()
        self.v.path('灵感库/外部坏文件.md').write_text('---\nunterminated',encoding='utf-8')
        self.assertEqual(self.v.run(dict(operation='search',query='学习',scope='owned'))['total'],1)

    def test_archive_does_not_expand_ownership(self):
        self.seed_scopes()
        a=self.v.read('资产/资产样本.md')
        self.change('archive',a)
        self.v.path('归档/不明来源.md').write_text('素材归档内容',encoding='utf-8')
        self.assertEqual(self.v.run(dict(operation='list',scope='owned'))['total'],2)
        self.assertEqual(self.v.run(dict(operation='list',scope='owned',include_archived=True))['total'],3)
        self.assertEqual(self.v.run(dict(operation='list',scope='all',include_archived=True))['total'],6)

    def test_invalid_scope_rejected(self):
        with self.assertRaises(ValueError):
            self.v.run(dict(operation='search',scope='anything'))

    def test_personal_save_dedup_does_not_read_external(self):
        self.v.path('资源/格式损坏的外部文件.md').write_text('---\nunclosed',encoding='utf-8')
        with patch.object(self.v,'read',wraps=self.v.read) as reader:
            note=self.create()
        self.assertEqual(note['scope'],'owned')
        self.assertTrue(all(Path(c.args[0]).parts[0] in ('项目','资产','技能与模板') for c in reader.call_args_list))

if __name__=='__main__':
    unittest.main(verbosity=2)
