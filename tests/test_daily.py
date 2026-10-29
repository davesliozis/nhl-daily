import importlib.util, unittest, datetime as dt
from unittest.mock import patch
from pathlib import Path
spec=importlib.util.spec_from_file_location('daily',Path(__file__).parents[1]/'scripts/daily.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
class Safeguards(unittest.TestCase):
 def event(self):
  return {'status':{'type':{'state':'pre'}},'competitions':[{'competitors':[{'homeAway':'away','team':{'location':'Away'}},{'homeAway':'home','team':{'location':'Home'}}]}]}
 def test_both_negative_favorite(self):
  book={'provider':{'name':'One book'},'moneyline':{'away':{'current':{'odds':'-120'}},'home':{'current':{'odds':'-140'}}}}
  self.assertEqual(d.moneyline({'pickcenter':[book]},self.event()),'Home (-140)')
 def test_dont_mix_incomplete_books(self):
  books=[{'provider':{'name':'A'},'moneyline':{'away':{'close':{'odds':'-160'}}}},{'provider':{'name':'B'},'moneyline':{'home':{'close':{'odds':'+130'}}}}]
  self.assertEqual(d.moneyline({'pickcenter':books},self.event()),'Odds not available')
 def test_completed_game_has_no_pregame_favorite(self):
  e=self.event();e['status']['type']['state']='post'
  self.assertEqual(d.moneyline({},e),'Odds not available')
 def test_no_guessed_goalie(self): self.assertEqual(d.goalie({}),'Not yet announced')
 def test_pacific_dst(self):
  self.assertEqual(d.iso('2026-11-02T03:00Z').astimezone(d.PT).hour,19)
  self.assertEqual(d.iso('2026-10-31T03:00Z').astimezone(d.PT).hour,20)
 def test_duplicate_does_not_send(self):
  with patch.dict(d.os.environ,{'SMTP_USERNAME':'test','GMAIL_APP_PASSWORD':'test','EMAIL_TO':'test'}),patch.object(d,'sent_exists',return_value=True),patch.object(d.smtplib,'SMTP_SSL') as smtp:
   d.send_report(dt.date(2026,10,28),'html');smtp.assert_not_called()
 def test_unsafe_duplicate_check_stops_send(self):
  with patch.dict(d.os.environ,{'SMTP_USERNAME':'test','GMAIL_APP_PASSWORD':'test','EMAIL_TO':'test'}),patch.object(d,'sent_exists',side_effect=RuntimeError('unavailable')),patch.object(d.smtplib,'SMTP_SSL') as smtp:
   with self.assertRaises(RuntimeError):d.send_report(dt.date(2026,10,28),'html')
   smtp.assert_not_called()
if __name__=='__main__':unittest.main()
