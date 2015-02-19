import unittest
import jsc.rparser as rp
import pyparsing


def cmp_lists(l1, l2):
    return not set(l1) ^ set(l2)


class TestRecipeParser(unittest.TestCase):
    def setUp(self):
        self.recipe = {
            'name recipename': ['name', 'recipename'],
            'name "recipe name"': ['name', 'recipe name'],
            'run ls -lah ./dir/path': ['run', 'ls -lah ./dir/path'],
            'run echo "echo"': ['run', 'echo "echo"'],
            'put /file "content with spaces in quotes"': ['put', '/file', 'content with spaces in quotes'],
            'put /file content': ['put', '/file', 'content'],
            'put /file unquoted_trailing_lonely_"': ['put', '/file', 'unquoted_trailing_lonely_"'],
            'put /file "escaped\\""': ['put', '/file', 'escaped"'],
            'replace /full/file/path "escaped\\"" "replace string"': ['replace', '/full/file/path', 'escaped"', 'replace string'],
            'replace relative_file "escaped\\"" "replace string"': ['replace', 'relative_file', 'escaped"', 'replace string'],
            'replace relative_file/path "escaped\\"" "replace string"': ['replace', 'relative_file/path', 'escaped"', 'replace string'],
            'replace relative_file/path "escaped\\"" "replace \nstring with newline"': ['replace', 'relative_file/path', 'escaped"', 'replace \nstring with newline'],
            'gd --depth=asdf --pkey=pkey --branch=name git@github.com/jumpstarter-io/jsc path/tocheck\ out/ya/\\nyah':
                ['gd', '--depth=asdf', '--pkey=pkey', '--branch=name', 'git@github.com/jumpstarter-io/jsc', 'path/tocheck out/ya/\nyah'],
            'gd git@github.com/jumpstarter-io/jsc path':
                ['gd', 'git@github.com/jumpstarter-io/jsc', 'path'],
            'gd --depth=asdf git@github.com/jumpstarter-io/jsc /path/tocheck\ out/ya/\\nyah':
                ['gd', '--depth=asdf', 'git@github.com/jumpstarter-io/jsc', '/path/tocheck out/ya/\nyah'],
            'install source/dir_\\nwith_escnewline dst # with comment end': ['install', 'source/dir_\nwith_escnewline', 'dst'],
            'install src dst': ['install', 'src', 'dst'],
        }

        self.should_fail = [
            'install src',
            'run',
            'append "sdfasdf\nasdfsdf"',
            'nonexisting "foo" foobar',
            'gd -depth path'

        ]

    def test_sl(self):
        for cmd in self.recipe.keys():
            result = rp.parse(cmd)
            if len(result) == 0:
                # comments does not return results
                return
            assert len(result) == 1
            assert cmp_lists(result.pop(), self.recipe[cmd])

    def test_ml(self):
        commands = []
        results = []
        for cmd in self.recipe.keys():
            commands.append(cmd)
            results.append(self.recipe[cmd])
        results.reverse()
        parse_results = rp.parse("\n".join(commands))
        for result in parse_results:
            assert cmp_lists(result, results.pop())
    """
    def test_sl_failing(self):
        for cmd in self.should_fail:
            try:
                print(rp.parse(cmd))
                # is this is reach, it parsed something...
                assert False
            except pyparsing.ParseException:
                pass
    """
    def test_ml_failing(self):
        rec = "\n".join(self.should_fail)
        try:
            print("parsed_ml_failing: {}".format(rp.parse(rec)))
            # is this is reach, it parsed something...
            assert False
        except pyparsing.ParseException:
            pass


if __name__ == '__main__':
    unittest.main()
