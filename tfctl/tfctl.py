import os
import boto3
import sys
import subprocess
import json
import yaml
import datetime
import shutil

user_home = os.path.expanduser('~')
tf_version = '0.13.4'
tf_arguments = ' '.join(sys.argv[3:])
tf_base_dir = os.path.join(user_home, '.terraform')
tf_bin_dir = os.path.join(tf_base_dir, 'bin')
tf_bin = os.path.join(tf_bin_dir, 'terraform')
tf_data_dir = os.path.join(tf_base_dir, 'data')
tf_download_address_tpl = 'https://releases.hashicorp.com/terraform/' \
                          '{0}/terraform_{0}_{1}_amd64.zip'
tf_vars_dir = os.path.join(os.getcwd(), 'vars')
tf_work_cmd_tpl = 'TF_DATA_DIR={0} {1} {2} {3} 2>&1 | tee /tmp/tf.log'
tf_init_cmd_tpl = 'TF_DATA_DIR={0} {1} init -backend-config="key={2}.tfstate"'
current_time = datetime.datetime.now().strftime('%Y-%m-%d_%H_%M_%S')
var_file_name = ''

if 'help' in sys.argv[1]:
    tf_cmd = 'help'
else:
    env_id = sys.argv[1]
    tf_cmd = sys.argv[2]
    tf_env_data_dir = os.path.join(tf_data_dir, env_id)
    tf_init_cmd = tf_init_cmd_tpl.format(tf_env_data_dir, tf_bin, env_id)
    var_file_name = '{0}.tfvars'.format(os.path.join(tf_vars_dir, env_id))


def get_command_output(cmd):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True)
    return result.stdout


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


if tf_cmd == 'get-ssh-key':
    ss = boto3.client('secretsmanager')
    secret_id = '{0}-ec2_ssh_key'.format(env_id)
    ssh_key_file_loc = os.path.join(user_home, '.ssh', '{0}.pem'.format(env_id))
    try:
        ssh_key = ss.get_secret_value(SecretId=secret_id)['SecretString']
        with open(ssh_key_file_loc, 'w') as ssh_key_file:
            ssh_key_file.write(ssh_key)
        os.chmod(ssh_key_file_loc, 600)
        print("key written to {0} and chmod'ed to 600".format(ssh_key_file_loc))
        exit(0)
    except ss.exceptions.ResourceNotFoundException:
        print('Could not find requested secret value, exiting...')
        exit(1)
elif tf_cmd == 'update-kubeconfig':
    tf_cmd = 'output'
    tf_arguments = '-json'
    tf_work_cmd = tf_work_cmd_tpl.format(tf_env_data_dir, tf_bin,
                                         tf_cmd, tf_arguments)
    target_clusters = []
    res = json.loads(get_command_output(tf_work_cmd))
    for key in res:
        if key.startswith('eks') and key.endswith('connect-info'):
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
        print("No EKS clusters description found, exiting...")
        exit(0)

tf_var_file_ref = ''
if tf_cmd not in ["help", "output", "taint", "untaint", "state", "import"]:
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
    if all([os.path.isdir(tf_bin_dir),
            os.path.exists(tf_bin),
            os.access(tf_bin, os.X_OK)]):
        print('terraform executable file found...')
        break
    else:
        tf_install_prompt = "terraform executable file not found, " \
                            "do you want install it [Y/n]"
        if input(tf_install_prompt).lower() in ['y', 'yes']:
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


def init_and_exec():
    os.system(tf_work_cmd)
    check = os.system('cat /tmp/tf.log | grep "terraform init"')
    if check == 0:
        init = os.system(tf_init_cmd)
        if init == 0:
            work = os.system(tf_work_cmd)
            if work != 0:
                os.system('cat /tmp/tf.log')


def main():
    if len(sys.argv) > 2 or 'help' in sys.argv[1]:
        init_and_exec()
    else:
        print("Not enough arguments, exiting...")
        exit(1)


if __name__ == '__main__':
    main()
