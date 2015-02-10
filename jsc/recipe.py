import os
from docopt import docopt, DocoptExit
try:
    import logger as log
except ImportError:
    from jsc import logger as log
import shlex


class RecipeRuntimeError(Exception):
    pass


def docopt_cmd(func):
    """
    This decorator is used to simplify the try/except block and pass the result
    of the docopt parsing to the called action.
    """
    def fn(remoto_exec, state, arg):
        # make sure it's of type str and not unicode/byte
        # shlex tokenizes the string like python does for sys.argv
        # arg = shlex.split("{}".format(arg))
        log.info(arg)
        arg = shlex.split(arg)
        opt = docopt(func.__doc__, arg)
        return func(remoto_exec, state, opt)
    return fn


@docopt_cmd
def rc_name(remoto_exec, state, args):
    """
    Usage:
      name <name>
    """
    state["name"] = args["<name>"]
    return state


@docopt_cmd
def rc_package(remoto_exec, state, args):
    """
    Usage:
      package <pkg>...
    """
    success, installed_packages = remoto_exec.package(args["<pkg>"])
    if not success:
        raise RecipeRuntimeError("package failed")
    if "package" not in state["software_list"]:
        state["software_list"]["package"] = {}
    for pkg in installed_packages.keys():
        state["software_list"]["package"][pkg] = {}
        state["software_list"]["package"][pkg]["version"] = installed_packages[pkg]
    return state


@docopt_cmd
def rc_gd(remoto_exec, state, args):
    """
    Usage:
      gd [--pkey=<pkey>] [--branch=<branch>] [--depth=<n>] <repo> <dst>
    """
    is_dev = state["is_dev"]
    repo = args["<repo>"]
    dst = args["<dst>"]
    depth = args["--depth"] if is_dev else 1
    branch = args["--branch"]
    pkey = args["--pkey"]
    success, git_res = remoto_exec.git_clone(repo, dst, depth, branch, pkey)
    commit, ref = git_res
    if success:
        if "gd" not in state["software_list"]:
            state["software_list"]["gd"] = {}
        state["software_list"]["gd"][args["<dst>"]] = {
            "ref": ref,
            "commit": commit,
            "repo": args["<repo>"]
            }
        if not is_dev:
            remoto_exec.rmtree(os.path.join(args["<dst>"], ".git"))
        return state
    raise RecipeRuntimeError("gd failed")


# no docopt_cmd since we want to forward the full argument line to remoto.rc_run
def rc_run(remoto_exec, state, cmd):
    """
    Usage:
      run <cmd>...
    """
    success, msg = remoto_exec.rc_run(cmd)
    if not success:
        raise RecipeRuntimeError(msg)
    else:
        log.white(msg)
    return state


@docopt_cmd
def rc_install(remoto_exec, state, args):
    """
    Usage:
      install <src> <dst>
    """
    success, msg = remoto_exec.install(args['<src>'], args['<dst>'])
    if not success:
        raise RecipeRuntimeError(msg)
    return state


@docopt_cmd
def rc_append(remoto_exec, state, args):
    """
    Usage:
      append <file> <text>
    """
    success, msg = remoto_exec.file_put(args['<file>'], args['<text>'])
    if not success:
        raise RecipeRuntimeError(msg)
    return state


@docopt_cmd
def rc_put(remoto_exec, state, args):
    """
    Usage:
      put <file> <text>
    """
    success, msg = remoto_exec.file_put(args['<file>'], args['<text>'], truncate=True)
    if not success:
        raise RecipeRuntimeError(msg)
    return state


@docopt_cmd
def rc_replace(remoto_exec, state, args):
    """
    Usage:
      replace <file> <find> <replace>
    """
    success, msg = remoto_exec.file_content_replace(args['<file>'], args['<find>'], args['<replace>'])
    if not success:
        raise RecipeRuntimeError(msg)
    return state


@docopt_cmd
def rc_insert(remoto_exec, state, args):
    """
    Usage:
      insert <file> <find> <insert>
    """
    success, msg = remoto_exec.file_content_replace(args['<file>'], args['<find>'], args['<find>']+args['<insert>'], accurances=1)
    if not success:
        raise RecipeRuntimeError(msg)
    return state


@docopt_cmd
def rc_rinsert(remoto_exec, state, args):
    """
    Usage:
      rinsert <file> <find> <insert>
    """
    success, msg = remoto_exec.file_content_replace(args['<file>'], args['<find>'], args['<insert>']+args['<find>'], accurances=1, reverse=True)
    if not success:
        raise RecipeRuntimeError(msg)
    return state


cmd_map = {
    "name": rc_name,
    "package": rc_package,
    "gd": rc_gd,
    "run": rc_run,
    "install": rc_install,
    "append": rc_append,
    "put": rc_put,
    "replace": rc_replace,
    "insert": rc_insert,
    "rinsert": rc_rinsert}


def recipe_execute_cmd(remoto_exec, state, line):
    line_words = line.split(' ')
    command_word = line_words[0]
    args = ' '.join(line_words[1:])
    state = cmd_map[command_word](remoto_exec, state, args)


def run(remoto_exec, recipe, is_dev):
    lc = 1
    state = {"software_list": {}, "name": None, "is_dev": is_dev}
    try:
        for line in recipe.splitlines(False):
            # Everything after # is comment.
            clean_line = line.strip().split("#")[0]
            if clean_line == '':
                continue
            recipe_execute_cmd(remoto_exec, state, clean_line)
            lc += 1
        return state
    except DocoptExit as e:
        log.err('Recipe contains an invalid command at Line {line}.'.format(line=lc))
        log.err(e)
    except RecipeRuntimeError as e:
        log.err('Recipe command on line {line} failed with error: {e}'.format(line=lc, e=e))
