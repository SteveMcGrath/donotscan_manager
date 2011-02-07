#!/usr/bin/env python

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy                 import Table, Column, Integer, String, \
                                       DateTime, Date, ForeignKey, \
                                       Boolean, create_engine, MetaData, and_
from sqlalchemy.orm             import relation, backref, sessionmaker
import datetime
import smtplib
import cmd
import sys
import os
import ConfigParser

version   = 'POCb2'
Base      = declarative_base()
motd      = '''
DoNotScan Manager Version %s
---------------------------------
POC Alpha Build.  No Information.
''' % version

def get_config(self):
  '''
  Retrieves the configuration file and returns a ConfigParser object.
  '''
  configfile = '/'.join(os.path.abspath(__file__).split('/')[:-1]) + '/config.ini'
  config = ConfigParser.ConfigParser()
  config.read(configfile)
  return config


def config_param(self, stanza, param):
  '''
  Returns a Configuration Parameter stored in the config file
  '''
  config  = get_config()
  return config.get(stanza, param)
  

def get_dbcon(self):
  '''
  Checks the database configuration
  '''
  config    = get_config()
  db_type   = config.get('Database', 'database_type')
  if db_type == 'sqlite':
    db_uri  = config.get('Database', 'location')
    c_name  = 'sqlite:///%s' % db_uri
  else:
    db_user   = config.get('Database', 'username')
    db_host   = config.get('Database', 'hostname')
    db_port   = config.get('Database', 'port_number')
    db_name   = config.get('Database', 'database')
    db_pass   = config.get('Database', 'password')
    if db_port is not '':
      c_name  = '%s://%s:%s@%s:%s/%s' % (db_type, db_user, db_pass, db_host, db_port, db_name)
    else:
      c_name  = '%s://%s:%s@%s/%s' % (db_type, db_user, db_pass, db_host, db_name)
  return c_name
  
  

class Rule(Base):
  '''
  This is the base rule class.  This object is what will primarially be
  worked with from the rest of the application.  Handles all of the basic info
  about the rule, including who requested, the expiration, and what the
  ticket number is.  All of the information is considered optional with the
  exception of the expiration, active flag, and the rule itself.
  '''
  __tablename__ = 'rules'
  
  id            = Column(Integer, primary_key=True)
  rule          = Column(String)
  ticket        = Column(String)
  name          = Column(String)
  email         = Column(String)
  application   = Column(String)
  reason        = Column(String)
  permanent     = Column(Boolean)
  active        = Column(Boolean)
  created       = Column(DateTime)
  expiration    = Column(Date)
  activity      = relation('Activity', order_by='Activity.id', backref='rule')
  
  def __init__(self, rule, ticket, name, email, application, reason, expiration=None, permanent=False):
    self.rule         = rule
    self.ticket       = ticket
    self.name         = name
    self.email        = email
    self.application  = application
    self.reason       = reason
    self.created      = datetime.datetime.now()
    self.active       = True
    self.permanent    = permanent
    if expiration is not None:
      self.expiration = expiration
    else:
      if self.permanent:
        self.expiration = datetime.date(datetime.date.today().year, 12, 31)
      else:
        self.expiration = datetime.date.today() + datetime.timedelta(14)
    self.activity       = [Activity(event='Created', info=None)]
  
  def __repr__(self):
    return "<Rule('%s', '%s', '%s')>" % (self.rule, self.ticket, self.expiration.isoformat())
  
  def reactivate(self):
    '''
    Will set the rule to active status.  this means that the rule will be
    included in the rule generation as long as it is not expired.
    '''
    self.active         = True
    self.activity.append(Activity(event='Re-Enabled'))
  
  def deactivate(self):
    '''
    Will set the rule to inactive status.  The rule will no longer be included
    in the rule generation reguardless of any other settings.
    '''
    self.active         = False
    self.activity.append(Activity(event='Disabled'))
  
  def true_up(self, expiration):
    '''
    Re-ups the expiration.  This is useful if the rule is still needed even
    though the expiration has been reached.  This is also the main component
    used for yearly true-ups of permanent rules.
    '''
    self.expiration     = expiration
    self.activity.append(Activity(event='Modified', info='Expiration set to %s' % expiration.isoformat()))
  

class Activity(Base):
  '''
  This class basically is designed to keep track of any changes to a rule
  object.
  '''
  __tablename__ = 'activity'
  
  id            = Column(Integer, primary_key=True)
  event         = Column(String)
  info          = Column(String)
  date          = Column(DateTime)
  rule_id       = Column(Integer, ForeignKey('rules.id'))
  #rule          = relation(Rule, backref=backref('activity', order_by=id))
  
  def __init__(self, event, info=None):
    self.event  = event
    self.info   = info
    self.date   = datetime.datetime.now()
  
  def __repr__(self):
    return "<Activity('%s', '%s', '%s)" % (self.event, self.info, self.date.isoformat())


class Rules(object):
  from_addr = config_param('Email', 'from_address')
  smtp_serv = config_param('Email', 'smtp_server')
  smtp_port = int(config_param('Email', 'smtp_port'))
  
  '''
  This is the 'middleware' layer of the application.  This will talk to the
  database and provide all of the functions needed for any front-end needed.
  No front-end specific code shouls exist here.
  '''
  def __init__(self, dbconnect, debug=False):
    self.engine   = create_engine(dbconnect, echo=debug)
    Rule.metadata.create_all(self.engine)
    Activity.metadata.create_all(self.engine)
    self.Session  = sessionmaker(bind=self.engine)
    session       = self.Session()
  
  def gen_donotscan(self):
    '''
    Generates the Do-Not-Scan list based on what rules are active and not
    expired.  IF a rule is active however expired, then notify the rule owner
    and deactivate the rule.
    '''
    donotscan = ''
    for rule in self.list():
      if rule.active:
        if rule.expiration >= datetime.date.today():
          donotscan += '%s\n' % rule.rule
        else:
          self.expiration_notification(rule.id)
          self.deactivate(rule.id)
    return donotscan
  
  def __send_email(self, to_addr, subject, message):
    
    msg = '''From: Do Not Scan Notifications <%s>
    To: %s
    Subject: %s
    
    %s
    ''' % (self.from_addr, to_addr, subject, message)
    try:
      mail  = smtplib.SMTP(self.smtp_serv, self.smtp_port)
      mail.sendmail(self.from_addr, [self.to_addr,], msg)
      return 'sent'
    except:
      return 'error'
    
  
  def rule_audit(self):
    '''
    This is primarially used during the yearly true-up.  All active rules
    will be mailed to their respective owners and those owners will be asked
    if the rules are still needed.  If this is the case, those rules need to
    be renewed for the next year or they will fall off.
    '''
    
    '''
    NOTE: Currently there isn't an easy way to automate this, so this means
    that the team in charge of managing the Do-Not-Scan list will need to
    touch every permanent rule in the database.  This can be automated in a
    more specific manner, however this would likely have to be custom-tailored
    to each deployment.
    '''
    # Get a list of all of the emails in the db
    # generate an email with a list of all of the rules, the Ticket Num, and
    # any other relevent information and email it out to each user.
    pass
  
  def expiration_notification(self, rule_id):
    '''
    Notified the owner of the rul that the rule has expired.  If the user does
    not request that the rule be renewed, then the rule will no longer be
    active.
    '''
    rule  = self.get(rule_id)
    
    msg   = '''
    %s,
    
    This is an automated response set to inform you that the do-not-scan rule
    listed below has expired and will no longer be excluded.  If you need to
    this exclusion to continue, please notify %s immediately in order to have
    this rule re-instated.
    
    Ticket Number : %s
      Application : %s
           Reason : %s
             Rule : %s
    ''' % (rule.name, self.from_addr, rule.ticket, rule.application, rule.reason, rule.rule)
    self.__send_email(rule.email, 'Scan Exclusion Expiration Notification', msg)
  
  def add(self, obj):
    '''
    Adds a Rule object into the database.
    '''
    session = self.Session()
    session.add(obj)
    session.commit()
    session.close()
  
  def get(self, rule_id):
    '''
    Returns the specified rule.
    '''
    session = self.Session()
    query   = session.query(Rule).filter(Rule.id=rule_id)
    x       = query.first()
    session.close()
    return x
  
  def search(self, **args):
    '''
    Searches the Rule database based on the parameters provided.
    '''
    session = self.Session()
    if len(args) == 1:
      query   = session.query(Rule).filter(**args)
    if len(args) > 1:
      query   = session.query(Rule).filter(and_(**args))
    x = query.all()
    session.close()
    return x
  
  def list(self, active=True, inactive=True):
    '''
    Returns a list of all rules.
    '''
    session = self.Session()
    query   = session.query(Rule).filter(Rule.active.in_([active, not(inactive)]))
    x = query.all()
    session.close()
    return x
  
  def activate(self, rule_id):
    '''
    Activates a rule.
    '''
    session = self.Session()
    rule    = session.query(Rule).filter(Rule.id = rule_id).first()
    rule.active = True
    session.commit()
    session.close()
  
  def deactivate(self, rule_id):
    '''
    Deactivates a rule.
    '''
    session = self.Session()
    rule    = session.query(Rule).filter(Rule.id = rule_id).first()
    rule.active = False
    session.commit()
    session.close()
    
  def trueup(self, rule_id):
    '''
    Renews a permanent rule.
    '''
    session = self.Session()
    rule    = session.query(Rule).filter(Rule.id = rule_id).first()
    today = datetime.date.today()
    if today.month > 6:
      expiry  = datetime.date(today.year+1, 12, 31)
    else:
      expiry  = datetime.date(today.year, 12, 31)
    
    rule.expiration = date
    session.commit()
    session.close()
  
  
class CLI(cmd.Cmd):
  intro   = motd
  prompt  = 'DoNotScan> '
  rules   = Rules(get_dbcon(), bool(config_param('General', 'debugging')))
  
  def __print_rules(self, rule_list):
    st = {True: 'Active', False: 'Inactive'}
    print '%4s %31s %15s %10s %29s %10s' % ('Id', 'Rule', 'Ticket', 'Expiration', 'Requestor', 'Status')
    print '%4s %31s %15s %10s %29s %10s' % ('-' * 4, '-' * 31, '-' * 15, '-' * 10, '-' * 29, '-' * 10)
    for rule in rule_list:
      if rule.permanent:
        print '%04d %31s %15s %10s %29s %10s' % (rule.id, rule.rule, rule.ticket, 'PERMANENT', rule.name, st[rule.active])
      else:
        print '%04d %31s %15s %10s %29s %10s' % (rule.id, rule.rule, rule.ticket, rule.expiration, rule.name, st[rule.active])
  
  def do_list(self, s):
    '''
    Prints the list of currently active rules.
    '''
    self.__print_rules(self.rules.list(inactive=False))
  
  def do_search(self, s):
    '''
    search [[PARAM=VALUE] [PARAM=VALUE] etc.]
    
    Searches the rule repository based on the specified criteria and returns
    the matching list.
    '''
    opts = {}
    for item in s.split():
      dset = item.strip().split('=')
      opts[dset[0]] = dset[1]
    self.__print_rules(self.rules.search(**opts))
  
  def do_trueup(self, s):
    self.rules.trueup(int(s))
  
  def do_deactivate(self, s):
    '''
    deactivate [rule_id]
    
    Deactivates a rule.
    '''
    self.rules.deactivate(int(s))
  
  def do_activate(self, s):
    '''
    activate [rule_id]
    
    Activates a rule
    '''
    self.rules.acivate(int(s))
  
  def do_new(self, s):
    '''
    new
    
    Generates a new rule.
    '''
    tf = {'n': False, 'y': True}
    
    ticket    = raw_input('   Ticket Number : ')
    name      = raw_input('  Requestor Name : ')
    email     = raw_input(' Requestor Email : ')
    app       = raw_input('     Application : ')
    reason    = raw_input('Exemption Reason : ')
    perm      = raw_input('Permanent? (y/N) : ').lower()
    if perm == '':
      perm    = False
    else:
      perm    = tf[perm[0]]
    if not perm:
      rdate   = raw_input(' Expiration Date : ')
      year, month, day  = rdate.split('-')
      expire  = datetime.date(int(year), int(month), int(day))
    else:
      expire  = None
    rule      = raw_input('            Rule : ')
    self.rules.add(Rule(rule, ticket, name, email, app, reason, expire, perm))
    
  
  def do_generate(self, s):
    '''
    Generates the Do-Not-Scan list.
    '''
    donotscan = self.rules.gen_donotscan()
    print donotscan
    
  
  def do_exit(self, s):
    '''
    Exits the script.
    '''
    sys.exit()

if __name__ == '__main__':
  if len(sys.argv) > 1:
    CLI().onecmd(' '.join(sys.argv[1:]))
  else:
    CLI().cmdloop()