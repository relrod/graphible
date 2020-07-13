# Rick Elrod <relrod@redhat.com>
# GPLv3+, same as Ansible.

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = '''
callback: html
type: aggregate
short_description: Generate pretty HTML with playbook information
description:
  - Generates a pretty HTML page with information about playbook execution.
requirements:
  - whitelist in configuration
  - jinja2
options:
  template_path:
    description: The path to the directory Jinja2 template(s)
    default: /nvme/gentoo/rick/dev/ansible/html/
    env:
      - name: HTML_TEMPLATE_PATH
    ini:
      - section: html
        key: template_path
  output_file_path:
    description: Where to output the resulting HTML file
    default: /nvme/gentoo/rick/dev/ansible/html/out.html
    env:
      - name: HTML_OUTPUT_FILE_PATH
    ini:
      - section: html
        key: output_file_path
'''


from ansible import context
from ansible.plugins.callback import CallbackBase
from datetime import datetime
import jinja2
import sys
import time

def pluralize(word, count):
    out = '{0} {1}'.format(count, word)
    if count == 0 or count > 1:
        return out + 's'
    return out

# Stolen from timer.py and modified
def duration(runtime):
    days = runtime.days
    hours = runtime.seconds // 3600
    minutes = (runtime.seconds // 60) % 60
    seconds = runtime.seconds % 60

    out_list = []

    if days:
        out_list.append(pluralize('day', days))

    if hours:
        out_list.append(pluralize('hour', hours))

    if minutes:
        out_list.append(pluralize('minute', minutes))

    if seconds:
        out_list.append(pluralize('second', seconds))

    if not out_list:
        return '{0} miliseconds'.format((runtime * 1000).seconds)

    return ','.join(out_list)

def js_time(dt):
    return 'moment.utc("{0}").toDate()'.format(dt.isoformat())

class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 1.0
    CALLBACK_TYPE = 'aggregate'
    CALLBACK_NAME = 'html'
    CALLBACK_NEEDS_WHITELIST = True

    def set_options(self, task_keys=None, var_options=None, direct=None):
        super(CallbackModule, self).set_options(
            task_keys=task_keys,
            var_options=var_options,
            direct=direct,
        )
        self.template_path = self.get_option('template_path')
        self.output_path = self.get_option('output_file_path')

    def __init__(self, *args, **kwargs):
        super(CallbackModule, self).__init__(*args, **kwargs)

        # This is just state-keeping
        self.playbook = None
        self.play = None

        # These actually get sent to the template
        self.times = {
            'playbook': {},
            'play': {},
            #'task': {},
            'runner': {},
        }
        self.playbooks = []
        self.playbook_results = {}
        self.task_warnings = {}
        self.task_deprecations = {}
        self.host_facts = {}
        self.tasks = {}
        self.css_classes = {}

    def v2_playbook_on_start(self, playbook):
        self.playbook = playbook
        self.playbooks.append(playbook)
        self.times['playbook'][playbook] = {
            'start': datetime.utcnow(),
        }
        self.playbook_results[self.playbook] = {}

    def v2_playbook_on_play_start(self, play):
        # I don't know of a callback for play end, so do this instead:
        if self.play is not None:
            self.times['play'][self.play]['end'] = datetime.utcnow()

        self.play = play
        self.playbook_results[self.playbook][self.play] = {}
        self.times['play'][self.play] = {
            'start': datetime.utcnow(),
        }

    # On task start per host
    def v2_runner_on_start(self, host, task):
        host_name = host.get_name()
        if task._uuid not in self.times['runner']:
            self.times['runner'][task._uuid] = {}
        self.times['runner'][task._uuid][host_name] = {
            'start': datetime.utcnow(),
        }
        if task._uuid not in self.playbook_results[self.playbook][self.play]:
            self.playbook_results[self.playbook][self.play][task._uuid] = {}

    # On task start in general
    #def v2_playbook_on_task_start(self, task, is_conditional):
    #    self.tasks[task._uuid] = task

    # This is called per task, per host
    def html_on_task_end(self, result, css_class):
        uuid = result._task._uuid
        host_name = result._host.get_name()
        self.times['runner'][uuid][host_name]['end'] = \
            datetime.utcnow()
        self.tasks[uuid] = result._task
        self.playbook_results[self.playbook][self.play][uuid][host_name] = {
            'result': result,
        }

        if uuid not in self.css_classes:
            self.css_classes[uuid] = {
                'general': 'success',
                'hosts': {},
            }

        if host_name not in self.css_classes[uuid]['hosts']:
            self.css_classes[uuid]['hosts'][host_name] = {}

        self.css_classes[uuid]['hosts'][host_name]['general'] = css_class
        self.css_classes[uuid]['hosts'][host_name]['icon'] = \
            'fa-check-square text-success'

        # We want to go in order and keep the worst state.
        if 'failed' in css_class:
            self.css_classes[uuid]['hosts'][host_name]['icon'] = \
                'fa-times text-danger'
            self.css_classes[uuid]['general'] = 'failed'
        elif 'changed' in css_class:
            self.css_classes[uuid]['hosts'][host_name]['icon'] = \
                'fa-wrench text-warning'
            if self.css_classes[uuid]['general'] != 'failed':
                self.css_classes[uuid]['general'] = 'changed'
        elif 'skipped' in css_class:
            self.css_classes[uuid]['hosts'][host_name]['icon'] = \
                'fa-fast-forward text-muted'
            if self.css_classes[uuid]['general'] not in ['failed', 'changed']:
                self.css_classes[uuid]['general'] = 'skipped'

        # Handle deprecations/warnings
        self.task_warnings[uuid] = result._result.get('warnings', [])
        self.task_deprecations[uuid] = result._result.get('deprecations', [])

        # This conditional copied from foreman plugin
        if 'ansible_facts' in result._result:
            if host_name not in self.host_facts:
                self.host_facts[host_name] = {}

            for k, v in result._result['ansible_facts'].items():
                self.host_facts[host_name][k] = v

    def v2_runner_on_failed(self, result, ignore_errors=False):
        return self.html_on_task_end(result, 'danger failed')

    def v2_runner_on_ok(self, result):
        return self.html_on_task_end(
            result,
            'warning changed' if result.is_changed() else 'success')

    def v2_runner_on_skipped(self, result):
        return self.html_on_task_end(result, 'muted skipped')

    def v2_runner_on_unreachable(self, result):
        return self.html_on_task_end(result, 'danger failed')

    # This is called after the last play is run.
    def v2_playbook_on_stats(self, stats):
        # Mark end time of last play
        if self.play is not None:
            self.times['play'][self.play]['end'] = datetime.utcnow()

        self.times['playbook'][self.playbook]['end'] = datetime.utcnow()

        # The CLI can be called with multiple playbooks, but I don't think
        # there's a good way to be notified of when we've run the last playbook.
        # If we're using the playbook cli (and not adhoc), then we just assume
        # that context.CLIARGS['args'] will be all the playbooks. If we're
        # running something else (like adhoc), then just assume there was only
        # one playbook and be done.
        if sys.argv[0] == 'ansible-playbook':
            if len(context.CLIARGS['args']) != len(self.playbooks):
                # If they aren't equal, it means there is another playbook to
                # run.
                return

        # Template prep
        template_path = self.template_path
        fs_loader = jinja2.FileSystemLoader(searchpath=template_path)
        env = jinja2.Environment(loader=fs_loader)
        template = env.get_template('index.html')

        try:
            with open(self.output_path, 'w+') as tmpl:
                tmpl.write(
                    template.render(
                        # Stuff accumulated above
                        playbooks=self.playbooks,
                        times=self.times,
                        host_facts=self.host_facts,
                        playbook_results=self.playbook_results,
                        tasks=self.tasks,
                        task_warnings=self.task_warnings,
                        task_deprecations=self.task_deprecations,
                        css_classes=self.css_classes,

                        # helpers
                        duration=duration,
                        js_time=js_time,
                        pluralize=pluralize,
                    )
                )
        except Exception as e:
            print(e)
            raise
