"""One style reaches all three prose pipelines; exact quotes remain evidence."""
import unittest
from unittest.mock import patch
from herdr_attention.config import Settings
from herdr_attention.notify_tiers import NOTIFY_TIERS, TIER_LABELS
from herdr_attention.notify_gate import NotifyGate
from herdr_attention.store import Store
from herdr_attention.writing import STYLE_LABELS, public_config, style_block
from tests.test_summaries import Rig, GOOD, reply
from tests.helpers import Clock, settings
from tests.web_harness import BROWSER, run
from tests.web_fixture import page_fixture
from tests.test_typing_survives_refresh import reading


class WritingStyle(unittest.TestCase):
    def test_default_validation_and_shared_fixed_names(self):
        conf=Settings()
        self.assertEqual(conf.get('writing.style'),'plain')
        self.assertEqual(public_config(conf)['style'],'plain')
        for value in STYLE_LABELS:
            self.assertEqual(conf.validate('writing.style',value,from_phone=True),value)
        with self.assertRaises(ValueError):
            conf.validate('writing.style','certified')
        conf.set_override('writing.style','ste100')
        block=style_block(conf)
        self.assertIn('approximate',block)
        self.assertIn('Do not claim ASD-STE100 compliance',block)
        self.assertIn('verbatim',block)
        for tier in NOTIFY_TIERS:
            self.assertIn(TIER_LABELS[tier],block)

    def rig(self, **kw):
        rig=Rig(**kw)
        self.addCleanup(rig.tmp.cleanup)
        self.addCleanup(rig.collector.store.close)
        return rig

    def test_summary_and_ask_use_identical_style_and_cache_separates_styles(self):
        rig=self.rig(runner=lambda cmd,prompt: reply({'answer':'Read the test failure.'}) if 'You answer a short question' in ' '.join(cmd) else reply(GOOD))
        collector=rig.collector
        rig.block()
        collector.request_summary(rig.identity,'refresh'); first,=rig.run_all()
        self.assertEqual(first['status'],'complete')
        rig.collector.settings.set_override('writing.style','ste100')
        collector.request_summary(rig.identity,'refresh'); second,=rig.run_all()
        self.assertEqual(second['status'],'complete')
        self.assertIsNone(second['cached_from'])
        expected=style_block(collector.settings)
        def system(call):
            return call['command'][call['command'].index('--system-prompt')+1]
        self.assertIn(expected,system(rig.calls[-1]))
        collector.request_summary(rig.identity,'refresh'); cached,=rig.run_all()
        self.assertIsNotNone(cached['cached_from'])
        collector.request_summary(rig.identity,'ask',question='What next?',context='1. Yes')
        answer,=rig.run_all()
        self.assertEqual(answer['status'],'complete')
        self.assertIn(expected,system(rig.calls[-1]))
        self.assertEqual(answer['summary']['answer'],'Read the test failure.')
        # Source-bound approval options remain exact despite the new style block.
        self.assertIn('1. Yes',second['summary']['needs_you']['options'])
        self.assertNotIn('3. Delete the database',second['summary']['needs_you']['options'])

    def test_notification_prose_uses_the_same_style(self):
        conf=settings(writing={'style':'ste100'},notify_gate={'enabled':True})
        store=Store(':memory:');self.addCleanup(store.close)
        gate=NotifyGate(store,conf,Clock())
        gate.queue([{'id':1,'identity':'fixture'}],'A test finished.')
        with patch('herdr_attention.notify_gate.invoke',return_value=({'send':True,'title':'Tests finished','body':'Read the result.','do':''},{})) as invoke:
            result=gate.run_job(gate.next_job())
        self.assertEqual(result['status'],'complete')
        self.assertIn(style_block(conf),invoke.call_args.kwargs['system_prompt'])

    @unittest.skipUnless(BROWSER,'no Chromium found')
    def test_setting_is_optional_and_plain_english_is_selected(self):
        r=reading(run(page_fixture(),'/settings',412,915,script="""
          const field=document.querySelector('select[data-setting="writing.style"]');
          document.body.dataset.reading=JSON.stringify({value:field.value,options:[...field.options].map(o=>o.textContent)});
        """))
        self.assertEqual(r['value'],'plain')
        self.assertEqual(r['options'],list(STYLE_LABELS.values()))
