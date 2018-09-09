from json import dumps, loads
from netmiko import file_transfer
from re import search
from requests import (
    get as rest_get,
    post as rest_post,
    put as rest_put,
    delete as rest_delete
)
from requests.auth import HTTPBasicAuth
from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    PickleType,
    String
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import relationship
from subprocess import check_output

from eNMS.base.custom_base import CustomBase
from eNMS.base.helpers import str_dict
from eNMS.scripts.connections import napalm_connection, netmiko_connection
from eNMS.scripts.properties import type_to_properties


class Job(CustomBase):

    __tablename__ = 'Job'

    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True)
    description = Column(String)
    type = Column(String)

    __mapper_args__ = {
        'polymorphic_identity': 'Job',
        'polymorphic_on': type
    }


class Script(Job):

    __tablename__ = 'Script'

    id = Column(Integer, ForeignKey('Job.id'), primary_key=True)
    tasks = relationship('ScriptTask', back_populates='script')

    __mapper_args__ = {
        'polymorphic_identity': 'script',
    }

    @property
    def properties(self):
        return {p: getattr(self, p) for p in type_to_properties[self.type]}

    @property
    def serialized(self):
        properties = self.properties
        properties['tasks'] = [obj.properties for obj in getattr(self, 'tasks')]
        return properties


class NetmikoConfigScript(Script):

    __tablename__ = 'NetmikoConfigScript'

    id = Column(Integer, ForeignKey('Script.id'), primary_key=True)
    vendor = Column(String)
    operating_system = Column(String)
    content = Column(String)
    driver = Column(String)
    global_delay_factor = Column(Float)
    device_multiprocessing = True

    __mapper_args__ = {
        'polymorphic_identity': 'netmiko_config',
    }

    def job(self, args):
        task, device, results, payloads = args
        try:
            netmiko_handler = netmiko_connection(self, device)
            netmiko_handler.send_config_set(self.content.splitlines())
            result = f'configuration OK:\n\n{self.content}'
            success = True
        except Exception as e:
            result = f'netmiko config did not work because of {e}'
            success = False
        try:
            netmiko_handler.disconnect()
        except Exception:
            pass
        results[device.name] = {
            'success': success,
            'payload': payloads,
            'logs': result
        }


class NetmikoValidationScript(Script):

    __tablename__ = 'NetmikoValidationScript'

    id = Column(Integer, ForeignKey('Script.id'), primary_key=True)
    vendor = Column(String)
    operating_system = Column(String)
    driver = Column(String)
    command1 = Column(String)
    command2 = Column(String)
    command3 = Column(String)
    content_match1 = Column(String)
    content_match2 = Column(String)
    content_match3 = Column(String)
    content_match_regex1 = Column(Boolean)
    content_match_regex2 = Column(Boolean)
    content_match_regex3 = Column(Boolean)
    device_multiprocessing = True

    __mapper_args__ = {
        'polymorphic_identity': 'netmiko_validation',
    }

    def job(self, args):
        task, device, results, payloads = args
        success, result = True, {}
        try:
            netmiko_handler = netmiko_connection(self, device)
            for i in range(1, 4):
                command = getattr(self, 'command' + str(i))
                if not command:
                    continue
                output = netmiko_handler.send_command(command)
                expected = getattr(self, 'content_match' + str(i))
                result[command] = {
                    'output': output,
                    'expected': expected
                }
                if getattr(self, 'content_match_regex' + str(i)):
                    if not bool(search(expected, str(output))):
                        success = False
                else:
                    if expected not in str(output):
                        success = False
        except Exception as e:
            results[device.name] = f'netmiko did not work because of {e}'
            success = False
        try:
            netmiko_handler.disconnect()
        except Exception:
            pass
        results[device.name] = {
            'success': success,
            'payload': payloads,
            'logs': result
        }


class FileTransferScript(Script):

    __tablename__ = 'FileTransferScript'

    id = Column(Integer, ForeignKey('Script.id'), primary_key=True)
    vendor = Column(String)
    operating_system = Column(String)
    driver = Column(String)
    source_file = Column(String)
    dest_file = Column(String)
    file_system = Column(String)
    direction = Column(String)
    overwrite_file = Column(Boolean)
    disable_md5 = Column(Boolean)
    inline_transfer = Column(Boolean)
    device_multiprocessing = True

    __mapper_args__ = {
        'polymorphic_identity': 'file_transfer',
    }

    def job(self, args):
        task, device, results, payloads = args
        try:
            netmiko_handler = netmiko_connection(self, device)
            transfer_dict = file_transfer(
                netmiko_handler,
                source_file=self.source_file,
                dest_file=self.dest_file,
                file_system=self.file_system,
                direction=self.direction,
                overwrite_file=self.overwrite_file,
                disable_md5=self.disable_md5,
                inline_transfer=self.inline_transfer
            )
            result = transfer_dict
            success = True
            netmiko_handler.disconnect()
        except Exception as e:
            result = f'netmiko config did not work because of {e}'
            success = False
        results[device.name] = {
            'success': success,
            'payload': payloads,
            'logs': result
        }


class NapalmConfigScript(Script):

    __tablename__ = 'NapalmConfigScript'

    id = Column(Integer, ForeignKey('Script.id'), primary_key=True)
    vendor = Column(String)
    operating_system = Column(String)
    action = Column(String)
    content = Column(String)
    device_multiprocessing = True

    __mapper_args__ = {
        'polymorphic_identity': 'napalm_config',
    }

    def job(self, args):
        task, device, results, payloads = args
        try:
            napalm_driver = napalm_connection(device)
            napalm_driver.open()
            config = '\n'.join(self.content.splitlines())
            getattr(napalm_driver, self.action)(config=config)
            napalm_driver.commit_config()
            napalm_driver.close()
        except Exception as e:
            result = f'napalm config did not work because of {e}'
            success = False
        else:
            result = f'configuration OK:\n\n{config}'
            success = True
        results[device.name] = {
            'success': success,
            'payload': payloads,
            'logs': result
        }


class NapalmGettersScript(Script):

    __tablename__ = 'NapalmGettersScript'

    id = Column(Integer, ForeignKey('Script.id'), primary_key=True)
    getters = Column(MutableList.as_mutable(PickleType), default=[])
    content_match = Column(String)
    content_match_regex = Column(Boolean)
    device_multiprocessing = True

    __mapper_args__ = {
        'polymorphic_identity': 'napalm_getters',
    }

    def job(self, args):
        task, device, results, payloads = args
        result = {}
        try:
            napalm_driver = napalm_connection(device)
            napalm_driver.open()
            for getter in self.getters:
                try:
                    result[getter] = getattr(napalm_driver, getter)()
                except Exception as e:
                    result[getter] = f'{getter} failed because of {e}'
            if self.content_match_regex:
                success = bool(search(self.content_match, str_dict(result)))
            else:
                success = self.content_match in str_dict(result)
            napalm_driver.close()
        except Exception as e:
            result = f'script did not work:\n{e}'
            success = False
        if isinstance(payloads, dict):
            payloads[self.name] = result
        else:
            payloads = {self.name: result}
        if 'logs' in results:
            results['logs'][device.name] = result
            results['payload'][device.name] = payloads
        else:
            results['logs'] = {device.name: result}
            results['payload'] = {device.name: payloads}
            results['expected'] = self.content_match
        if 'success' not in results or results['success']:
            results['success'] = success


class AnsibleScript(Script):

    __tablename__ = 'AnsibleScript'

    id = Column(Integer, ForeignKey('Script.id'), primary_key=True)
    vendor = Column(String)
    operating_system = Column(String)
    playbook_path = Column(String)
    arguments = Column(String)
    content_match = Column(String)
    content_match_regex = Column(Boolean)
    options = Column(MutableDict.as_mutable(PickleType), default={})
    pass_device_properties = Column(Boolean)
    inventory_from_selection = Column(Boolean)
    device_multiprocessing = True

    __mapper_args__ = {
        'polymorphic_identity': 'ansible_playbook',
    }

    def job(self, args):
        task, device, results, payloads = args
        try:
            arguments = self.arguments.split()
            command = ['ansible-playbook']
            if self.pass_device_properties:
                command.extend(['-e', dumps(device.properties)])
            if self.inventory_from_selection:
                command.extend(['-i', device.ip_address + ','])
            command.append(self.playbook_path)
            output = check_output(command + arguments)
            try:
                output = output.decode('utf-8')
            except AttributeError:
                pass
            if self.content_match_regex:
                success = bool(search(self.content_match, str(output)))
            else:
                success = self.content_match in str(output)
            results[device.name] = {
                'success': success,
                'payload': payloads,
                'logs': output
            }
        except Exception as e:
            results[device.name] = {
                'success': False,
                'payload': payloads,
                'logs': str(e)
            }


class RestCallScript(Script):

    __tablename__ = 'RestCallScript'

    id = Column(Integer, ForeignKey('Script.id'), primary_key=True)
    call_type = Column(String)
    url = Column(String)
    payload = Column(MutableDict.as_mutable(PickleType), default={})
    content_match = Column(String)
    content_match_regex = Column(Boolean)
    username = Column(String)
    password = Column(String)
    device_multiprocessing = False
    request_dict = {
        'GET': rest_get,
        'POST': rest_post,
        'PUT': rest_put,
        'DELETE': rest_delete
    }

    __mapper_args__ = {
        'polymorphic_identity': 'rest_call',
    }

    def job(self, task, results, payloads):
        try:
            if self.call_type in ('GET', 'DELETE'):
                result = self.request_dict[self.call_type](
                    self.url,
                    headers={'Accept': 'application/json'},
                    auth=HTTPBasicAuth(self.username, self.password)
                ).json()
            else:
                result = loads(self.request_dict[self.call_type](
                    self.url,
                    data=dumps(self.payload),
                    auth=HTTPBasicAuth(self.username, self.password)
                ).content)
            if self.content_match_regex:
                success = bool(search(self.content_match, str(result)))
            else:
                success = self.content_match in str(result)
            if isinstance(payloads, dict):
                payloads[self.name] = result
            else:
                payloads = {self.name: result}
        except Exception as e:
            result, success = str(e), False
        return {
            'success': success,
            'payload': payloads,
            'logs': result
        }


type_to_class = {
    'netmiko_config': NetmikoConfigScript,
    'netmiko_validation': NetmikoValidationScript,
    'napalm_config': NapalmConfigScript,
    'file_transfer': FileTransferScript,
    'napalm_getters': NapalmGettersScript,
    'ansible_playbook': AnsibleScript,
    'rest_call': RestCallScript
}
