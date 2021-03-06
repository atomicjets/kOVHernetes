"""Manage Kubernetes clusters on the OVH Cloud platform

Usage:
  kovh [options] <command> [<arg>...]
  kovh -h | --help

Options:
  -c, --config FILE   Alternate configuration file for the OVH client [default: kovh.conf]
  -h, --help          Show this screen
  -V, --version       Display version

Commands:
  auth      Credential management
  project   Cloud project administration
  create    Create Kubernetes cluster
  destroy   Destroy Kubernetes cluster

Use 'kovh <command> -h' for more information about a given command.
"""

from docopt         import docopt
from ovh            import APIError, ResourceNotFoundError
from inspect        import cleandoc
from json           import dumps
from sys            import exit
from os             import getlogin
from os.path        import realpath, expanduser
from time           import sleep
from ipaddress      import IPv4Network
#from OpenSSL.crypto import dump_certificate, dump_privatekey, FILETYPE_PEM

from .       import __version__
from .       import project
from .       import infra
from .client import Client
from .host   import Host
from .ca     import CA
from .auth   import get_current_cred


def main():
    args = docopt(__doc__,
                   version='kOHVernetes version {}'.format(__version__),
                   options_first=True)

    # create a client using configuration
    c = Client(config_file=realpath(args.get('--config')))
    if not (c._application_key and c._application_secret):
        print("Missing 'application_key' and/or 'application_secret' from configuration")
        print('Create a new application at:')
        print(' * {}/createApp'.format(c._endpoint[:-4]))
        exit(1)

    command = args['<command>']

    # TODO: check credential validity on APIError, too expensive to check before every command
    #if not has_valid_cred(c):
    #    print('Authentication denied')
    #    print("Verify token validity with 'kovh auth show'")
    #    exit(1)
    if command == 'auth':
        auth_command(c, args['<arg>'])
    elif command == 'project':
        project_command(c, args['<arg>'])
    elif command == 'create':
        create_command(c, args['<arg>'])
    elif command == 'destroy':
        destroy_command(c, args['<arg>'])


def auth_command(client, args):
    """Interact with the OVH authentication API

    Usage: auth <command>

    Commands:
      renew   Request a new consumer key
      show    Display information about current OVH credential
    """
    args = docopt(cleandoc(auth_command.__doc__), args)

    command = args['<command>']

    if command == 'show':
        try:
            cred = get_current_cred(client)
        except APIError as e:
            print('Failed authentication:')
            print(e)
        else:
            print('Authentication token accepted')
            print('Status: {}'.format(cred['status']))
            print('Expires on: {}'.format(cred['expiration'] if cred['expiration'] else '-'))
            print('Permissions: {}'.format(dumps(cred['rules'], indent=4)))

    elif command == 'renew':
        # default set of access rules
        # equivalent to ConsumerKeyRequest.add_recursive_rules(ovh.API_READ_WRITE, '/cloud')
        access_rules = [
            {'method': 'GET'   , 'path': '/cloud/*'},
            {'method': 'POST'  , 'path': '/cloud/*'},
            {'method': 'PUT'   , 'path': '/cloud/*'},
            {'method': 'DELETE', 'path': '/cloud/*'}
        ]

        print('Requesting new API token')
        try:
            ck_validation = client.request_consumerkey(access_rules)
        except APIError as e:
            print('Request failed:')
            print(e)
        else:
            print('Please visit the following URL to authenticate:')
            print(' * {}'.format(ck_validation['validationUrl']))
            print('Then add the following consumer key to your OVH configuration and try again:')
            print(' * {}'.format(ck_validation['consumerKey']))


def project_command(client, args):
    """Get information about cloud projects

    Usage: project <command>

    Commands:
      flavors     List available instance flavors for active project
      images      List available OS images for active project
      instances   List existing instances in active project
      keys        List available SSH keys for active project
      networks    List existing networks in active project
      regions     List available regions for active project
      services    List all OVH cloud projects (services)
      snapshots   List available snapshots for active project
      show        Display active project configuration
      usage       Show costs of active project for the current month
    """
    args = docopt(cleandoc(project_command.__doc__), args)

    command = args['<command>']

    # TODO: need a command dispatcher there
    # https://github.com/ansible/ansible/blob/5553b20/lib/ansible/module_utils/basic.py#L776-L789

    # TODO: catch APIError exceptions

    if command == 'show':
        print('Project: {}'.format(client._project if client._project else '-'))
        print('Region: {}'.format(client._region if client._region else '-'))
        print('SSH key: {}'.format(client._sshkey if client._sshkey else '-'))
    elif command == 'services':
        print(project.get_services(client))
    else:
        missing_params = client.missing_params(['project'])
        if missing_params:
            print('Missing parameters from configuration:', ', '.join(["'{}'".format(x) for x in missing_params]))
            exit(1)

        if command == 'flavors':
            print(project.get_flavors(client))
        elif command == 'images':
            print(project.get_images(client))
        elif command == 'instances':
            print(project.get_instances(client))
        elif command == 'keys':
            print(project.get_keys(client))
        elif command == 'networks':
            print(project.get_networks(client))
        elif command == 'regions':
            print(project.get_regions(client))
        elif command == 'snapshots':
            print(project.get_snapshots(client))
        elif command == 'usage':
            print(project.get_usage(client))


def create_command(client, args):
    """Create a Kubernetes cluster

    Usage: create -n NAME [-s SIZE]

    Options:
      -n, --name NAME   Cluster name
      -s, --size SIZE   Cluster size [default: 3]
    """
    args = docopt(cleandoc(create_command.__doc__), args)

    missing_params = client.missing_params(['project', 'region', 'sshkey', 'flavor'])
    if missing_params:
        print('Missing parameters from configuration:', ', '.join(["'{}'".format(x) for x in missing_params ]))
        exit(1)

    name = args['--name']
    longname = 'kovh:{}:'.format(name)
    try:
        size = int(args['--size'])
    except ValueError as e:
        print("Option --size expects a number, got '{}'".format(args['--size']))
        exit(1)

    try:
        pub_net_id = project.get_public_networks(client)[0]
    except APIError as e:
        print(e)
        exit(1)

    try:
        vlan_id = infra.next_vlan(client)
    except APIError as e:
        print(e)
        exit(1)

    # TODO: rollback on failure

    print("Creating private network '{}' with VLAN id {}".format(longname, vlan_id), end='', flush=True)
    try:
        priv_net = infra.create_priv_network(client, longname, vlan_id)
    except APIError as e:
        print(e)
        exit(1)

    print('\t[OK]')

    print("Waiting for readiness of private network '{}'".format(longname), end='', flush=True)

    network_active = False
    while not network_active:
        try:
            network_detail = client.get('/cloud/project/{}/network/private/{}'.format(client._project, priv_net['id']))
            if network_detail.get('status') == 'ACTIVE':
                network_active = True
        except ResourceNotFoundError:
            pass

        print('.', end='', flush=True)
        sleep(1)

    print('\t[OK]')

    subnet = IPv4Network('192.168.0.0/27')

    print('Creating subnet', end='', flush=True)
    try:
        infra.create_subnet(client, priv_net['id'], subnet)
    except APIError as e:
        print(e)
        exit(1)

    print('\t[OK]')

    print('Creating Certificate Authority', end='', flush=True)
    k8s_ca = CA()
    print('\t[OK]')

    print('Generating User Data', end='', flush=True)
    hosts = subnet.hosts()
    for _ in range(10):
        next_ip = next(hosts)

    master = Host(
        name='{}:master'.format(longname),
        roles=['master', 'node'],
        pub_net=pub_net_id,
        priv_net=priv_net['id'],
        client=client,
        ca=k8s_ca,
        ip=str(next_ip)
    )
    for c in ('kubelet', 'proxy', 'controller-manager', 'scheduler'):
        master.userdata.gen_kubeconfig(c)

    nodes = []
    for i in range(1, size):
        next_ip = next(hosts)
        node = Host(
            name='{}:node{:02}'.format(longname, i),
            roles=['node'],
            pub_net=pub_net_id,
            priv_net=priv_net['id'],
            client=client,
            ca=k8s_ca,
            ip=str(next_ip)
        )
        for c in ('kubelet', 'proxy'):
            node.userdata.gen_kubeconfig(c, 'host-' + master.ip.replace('.', '-'))
        nodes.append(node)

    print('\t[OK]')

    print('Creating instances', end='', flush=True)
    try:
        client.post('/cloud/project/{}/instance'.format(client._project), **master.make_body())
    except APIError as e:
        print(e)
        exit(1)

    for node in nodes:
        try:
            client.post('/cloud/project/{}/instance'.format(client._project), **node.make_body())
        except APIError as e:
            print(e)
            exit(1)

    print('\t[OK]')

    # TODO: generate local kubeconfig file
    #print('Creating local kubeconfig', end='', flush=True)
    #cli_key, cli_crt = k8s_ca.create_client_pair('system:masters', getlogin())
    #cli_key_pem = dump_privatekey(FILETYPE_PEM, cli_key)
    #cli_crt_pem = dump_certificate(FILETYPE_PEM, cli_crt)
    #ca_crt_pem = dump_certificate(FILETYPE_PEM, k8s_ca.cert)

    #with open(expanduser('~/.kube/kovh-{}-config'.format(name)), 'w') as kubeconfig:
    #    print((cli_key_pem + cli_crt_pem + ca_crt_pem).decode(), file=kubeconfig)

    #print('\t[OK]')
    #print('~/.kube/kovh-{}-config'.format(name))

def destroy_command(client, args):
    """Destroy a Kubernetes cluster

    Usage: destroy -n NAME

    Options:
      -n, --name NAME   Cluster name
    """
    args = docopt(cleandoc(destroy_command.__doc__), args)

    missing_params = client.missing_params(['project'])
    if missing_params:
        print('Missing parameters from configuration:', ', '.join(["'{}'".format(x) for x in missing_params ]))
        exit(1)

    longname = 'kovh:{}:'.format(args['--name'])

    try:
        del_instances = infra.get_cluster_instances(client, longname)
    except APIError as e:
        print(e)
        exit(1)

    if del_instances:
        for inst in del_instances:
            print("Destroying instance '{}'".format(inst['name']), end='', flush=True)
            try:
                client.delete('/cloud/project/{}/instance/{}'.format(client._project, inst['id']))
            except APIError as e:
                print(e)
                exit(1)
            print('\t[OK]')

        print('Waiting for instances termination', end='', flush=True)

        # TODO: check for deletion asynchronously
        for inst in del_instances:
            instance_deleted = False
            while not instance_deleted:
                try:
                    instance_detail = client.get('/cloud/project/{}/instance/{}'.format(client._project, inst['id']))
                    if instance_detail.get('status') == 'DELETED':
                        instance_deleted = True
                except ResourceNotFoundError:
                    instance_deleted = True

                print('.', end='', flush=True)
                sleep(1)

        print('\t[OK]')

    try:
        del_networks = infra.get_cluster_networks(client, longname)
    except APIError as e:
        print(e)
        exit(1)

    for netw in del_networks:
        print("Destroying private network '{}'".format(netw['name']), end='', flush=True)
        client.delete('/cloud/project/{}/network/private/{}'.format(client._project, netw['id']))
        print('\t[OK]')


if __name__ == '__main__':
    main()
