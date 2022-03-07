import os
import stat

import boto3
import sys
import subprocess
import json
import yaml
import datetime
import shutil

bash_completion_script = '''
_show_complete()
{
    local cur prev opts env_names
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    env_names=$( find vars/*.tfvars | sed -e 's/vars\///' | sed -e 's/\.tfvars//')


    if [[ ${cur} == -* ]] ; then
        COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
        return 0
    fi

    COMPREPLY=( $(compgen -W "${env_names}" -- ${cur}) )
}

complete -F _show_complete tfctl
'''

user_home = os.path.expanduser('~')
tf_version = '1.1.2'
tf_arguments = ' '.join(sys.argv[3:])
tf_base_dir = os.path.join(user_home, '.terraform')
tf_bin_dir = os.path.join(tf_base_dir, 'bin')
tf_bin = os.path.join(tf_bin_dir, 'terraform')
tf_data_dir = os.path.join(tf_base_dir, 'data')
tf_download_address_tpl = 'https://releases.hashicorp.com/terraform/' \
                          '{0}/terraform_{0}_{1}_amd64.zip'
tf_vars_dir = os.path.join(os.getcwd(), 'vars')
tf_work_cmd_tpl = 'TF_DATA_DIR={0} {1} {2} {3} {4} 2>&1 | tee /tmp/tf.log'
tf_init_cmd_tpl = 'TF_DATA_DIR={0} {1} init -backend-config="{3}={2}"'
tf_remote_state_key = 'key'
current_time = datetime.datetime.now().strftime('%Y-%m-%d_%H_%M_%S')
var_file_name = ''
tf_env_data_dir = ''
tf_init_cmd = ''
env_id = ''
tf_cmd = ''

if 'bash-completion' in sys.argv[1]:
    bash_completion_file_loc = os.path.dirname(__file__)
    bash_completion_file_name = os.path.join(bash_completion_file_loc,
                                             'tfctl.bash-completion')
    with open(bash_completion_file_name, 'w+') as bash_completion_file:
        bash_completion_file.write(bash_completion_script)
    os.chmod(bash_completion_file_name, 0o755)

    print("add\nsource {0}/tfctl.bash-completion\nat the end of "
          "your shell 'rc' file "
          "(~/.bashrc, ~/.zshrc, etc...)".format(bash_completion_file_loc))
    exit(0)


if 'help' in sys.argv[1]:
    tf_cmd = 'help'
else:
    env_id = sys.argv[1]
    tf_cmd = sys.argv[2]
    if tf_cmd in ['update-kubeconfig', 'get-ssh-keys']:
        tf_work_cmd_tpl = tf_work_cmd_tpl.replace('| tee', '>')
    tf_env_data_dir = os.path.join(tf_data_dir, env_id)
    with open('backend.tf') as backend_file:
        backend_file_content = backend_file.read().split('\n')
        if backend_file_content[0].startswith('###'):
            tf_remote_state_key = backend_file_content[0].split('=')[1].strip()
    tf_init_cmd = tf_init_cmd_tpl.format(tf_env_data_dir, tf_bin,
                                         env_id, tf_remote_state_key)
    var_file_name = '{0}.tfvars'.format(os.path.join(tf_vars_dir, env_id))


def init_and_exec(command):
    os.system(command)
    check = os.system('cat /tmp/tf.log | grep "terraform init"')
    if check == 0:
        init = os.system(tf_init_cmd)
        if init == 0:
            work = os.system(tf_work_cmd)
            if work != 0:
                os.system('cat /tmp/tf.log')
                exit(1)
    with open('/tmp/tf.log') as tf_log_file:
        return tf_log_file.read()


def update_kube_config(kube_info):
    kube_conf_loc = os.path.join(os.path.expanduser('~'), '.kube', 'config')
    kube_conf_bkp_loc = '{0}_tfctl_{1}'.format(kube_conf_loc, current_time)
    current_kube_config = {}
    try:
        if not os.path.isdir(os.path.dirname(kube_conf_loc)):
            os.makedirs(os.path.dirname(kube_conf_loc), exist_ok=True)
        shutil.copy(kube_conf_loc, kube_conf_bkp_loc)
        with open(kube_conf_loc) as kube_config:
            current_kube_config = yaml.load(kube_config.read(),
                                            Loader=yaml.SafeLoader)
    except FileNotFoundError:
        pass

    cluster_exists = False
    if len(current_kube_config) > 0:
        for current_cluster in current_kube_config['clusters']:
            if current_cluster['name'] == kube_info['value']['name'][0]:
                cluster_props = current_cluster['cluster']
                cluster_props['certificate-authority-data'] = kube_info['value']['cert']
                cluster_props['server'] = kube_info['value']['endpoint']
                cluster_exists = True
                break
    else:
        current_kube_config['apiVersion'] = 'v1'
        current_kube_config['kind'] = 'Config'
        current_kube_config['preferences'] = {}
    if not cluster_exists:
        kube_info_cert = kube_info['value']['cert'][0][0]['data']
        cluster_desc = {
            'name': kube_info['value']['name'][0],
            'cluster': {
                'certificate-authority-data': kube_info_cert,
                'server': kube_info['value']['endpoint'][0]
            }
        }
        context_desc = {
            'name': kube_info['value']['name'][0],
            'context': {
                'cluster': kube_info['value']['name'][0],
                'namespace': 'default',
                'user': kube_info['value']['name'][0]
            }
        }

        user_desc = {
            'name': kube_info['value']['name'][0],
            'user': {
                'exec': {
                    'apiVersion': 'client.authentication.k8s.io/v1alpha1',
                    'args': ['token', '-i', kube_info['value']['name'][0]],
                    'command': 'aws-iam-authenticator',
                    'env': None
                }
            }
        }

        if 'clusters' in current_kube_config:
            current_kube_config['clusters'].append(cluster_desc)
        else:
            current_kube_config['clusters'] = [cluster_desc]

        if 'contexts' in current_kube_config:
            current_kube_config['contexts'].append(context_desc)
        else:
            current_kube_config['contexts'] = [context_desc]

        if 'users' in current_kube_config:
            current_kube_config['users'].append(user_desc)
        else:
            current_kube_config['users'] = [user_desc]
        if 'current-context' not in current_kube_config:
            current_kube_config['current-context'] = kube_info['value']['name'][0]

        with open(kube_conf_loc, 'w') as kube_config:
            kube_config.write(yaml.safe_dump(current_kube_config))
    return 0


if tf_cmd == 'get-ssh-keys':
    tf_cmd = 'output'
    tf_arguments = '-json'
    tf_work_cmd = tf_work_cmd_tpl.format(tf_env_data_dir, tf_bin,
                                         tf_cmd, tf_arguments, '')
    target_clusters = []
    res = {}
    try:
        res = json.loads(init_and_exec(tf_work_cmd))
    except json.decoder.JSONDecodeError:
        exit(1)
    for output in res.keys():
        if output.startswith('ssh_key'):
            ssh_key = res[output]['value']
            ssh_key_file_loc = os.path.join(user_home, '.ssh', '{0}{1}.pem'.format(env_id, output.replace('ssh_key', '')))
            try:
                with open(ssh_key_file_loc, 'w') as ssh_key_file:
                    ssh_key_file.write(ssh_key)
                os.chmod(ssh_key_file_loc, 0o600)
                print("key written to {0} and chmod'ed to 600".format(ssh_key_file_loc))
            except PermissionError:
                print('Could not open key file "{0}" for write, exiting...'.format(ssh_key_file_loc))
                exit(1)
    exit(0)
elif tf_cmd == 'update-kubeconfig':
    tf_cmd = 'output'
    tf_arguments = '-json'
    tf_work_cmd = tf_work_cmd_tpl.format(tf_env_data_dir, tf_bin,
                                         tf_cmd, tf_arguments, '')
    target_clusters = []
    res = {}
    try:
        res = json.loads(init_and_exec(tf_work_cmd))
    except json.decoder.JSONDecodeError:
        exit(1)
    if len(res) > 0:
        for key in res:
            if key.startswith('k8s') and key.endswith('connect-info'):
                if all([len(res[key]['value']['cert']) > 0,
                        len(res[key]['value']['endpoint']) > 0,
                        len(res[key]['value']['name']) > 0]):
                    target_clusters.append(res[key])

    if len(target_clusters) > 0:
        for cluster in target_clusters:
            res = update_kube_config(cluster)
            if res != 0:
                print('Error updating kubeconfig with cluster {}'.format(
                    cluster['value']['name'][0])
                )
                exit(1)
        print('kube config updated with {0} clusters'.format(len(target_clusters)))
        exit(0)
    else:
        print("No k8s clusters description found, exiting...")
        exit(0)

tf_var_file_ref = ''
if tf_cmd not in ["output", "taint", "untaint", "state", "import"]:
    tf_var_file_ref = "--var-file={0}.tfvars".format(os.path.join(tf_vars_dir,
                                                               env_id))
if tf_cmd not in ["help"]:
    tf_work_cmd = tf_work_cmd_tpl.format(tf_env_data_dir, tf_bin,
                                     tf_cmd, tf_arguments, tf_var_file_ref)
else:
    tf_work_cmd = '{0} {1}'.format(tf_bin, tf_cmd)

tf_platform = 'linux'
if sys.platform.startswith('darwin'):
    tf_platform = 'darwin'
elif sys.platform.startswith('freebsd'):
    tf_platform = 'freebsd'
elif sys.platform.startswith('win32'):
    tf_platform = 'windows'
elif sys.platform.startswith('solaris'):
    tf_platform = 'solaris'

START_CWD = os.getcwd()

while True:
    if all([os.path.exists(tf_bin),
            os.path.isdir(tf_bin_dir),
            os.access(tf_bin, os.X_OK)]):
        print('terraform executable file found...')
        break
    else:
        print("terraform executable file not found, installation...")
        os.makedirs(tf_bin_dir, exist_ok=True)
        tf_download_address = tf_download_address_tpl.format(tf_version,
                                                             tf_platform)
        os.system('wget {0} -O /tmp/tf.zip'.format(tf_download_address))
        unzip_cmd_tpl = 'unzip -o /tmp/tf.zip -d {0} > /dev/null'
        res = os.system(unzip_cmd_tpl.format(tf_bin_dir))
        if res != 0:
            print("error while terraform executable file installation")
if tf_cmd not in ["help"]:
    if os.path.isfile(var_file_name):
        print("varilables file found...")
    else:
        print("ERROR: varilables file not found,")
        print("looking for {0}".format(var_file_name))
        print("exiting...")
        exit(1)

    if os.path.isdir(tf_env_data_dir):
        print("directory for {0} terraform data found...".format(env_id))
    else:
        print("directory for {0} terraform data not found, creating".format(env_id))
        os.makedirs(tf_env_data_dir)


def main():
    if len(sys.argv) > 2 or 'help' in sys.argv[1]:
        init_and_exec(tf_work_cmd)
    else:
        print("Not enough arguments, exiting...")
        exit(1)


if __name__ == '__main__':
    main()
