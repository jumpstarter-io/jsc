from docopt import docopt, DocoptExit
try:
    import logger as log
    import rparser
except ImportError:
    from jsc import logger as log, rparser


class RecipeRuntimeError(BaseException):
    pass


def run(rpc, recipe, is_dev):
    lc = 0
    state = {"software_list": {}, "name": None, "is_dev": is_dev}
    try:
        for statement in rparser.parse(recipe):
            # Everything after # is comment.
            lc += 1
            command = statement[0]
            args = statement[1:]
            state = rpc.call("rc_" + command, {"args": args, "state": state})
            # recipe_execute_cmd(rpc, state, clean_line)
        return state
    except DocoptExit as e:
        log.err('Recipe contains an invalid command at Line {line}.'.format(line=lc))
        log.err(e)
    except RecipeRuntimeError as e:
        log.err('Recipe command on line {line} failed with error: {e}'.format(line=lc, e=e))
