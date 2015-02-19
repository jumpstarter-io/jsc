import pyparsing as pp


pp.ParserElement.setDefaultWhitespaceChars(" \t\r")


def replace_lamba(find, replace):
    return lambda s, locs, toks: [toks[0].replace(find, replace)]


def parse(recipe):
    """ Parses recipe files
    """
    """
    sgl_quoted_string = "'", { "\'" | all characters - "'" }, '"'
    dbl_quoted_string = '"', { '\"' | all characters - '"' }, '"'
    ml_string ::= sgl_quoted_string | dbl_quoted_string
    """
    sgl_quoted_ml_string = pp.QuotedString("'", escChar="\\", multiline=True)
    dbl_quoted_ml_string = pp.QuotedString("\"", escChar="\\", multiline=True)
    quoted_ml_string = sgl_quoted_ml_string | dbl_quoted_ml_string

    sgl_quoted_sl_string = pp.QuotedString("'", escChar="\\")
    dbl_quoted_sl_string = pp.QuotedString("\"", escChar="\\")
    quoted_sl_string = sgl_quoted_sl_string | dbl_quoted_sl_string

    unquoted_sl_string = pp.Regex("[^ \n]+")

    sl_string = quoted_sl_string | unquoted_sl_string
    string = quoted_ml_string | sl_string

    """
    escaped_space ::= '\ ';
    fn_word ::= all characters - ' ';
    unix_path ::= <escaped_space> | <fn_word>, { escaped_space | <fn_word> };
    """
    escaped_space = pp.Regex("\\\\ ").addParseAction(replace_lamba("\\", ""))
    escaped_newline = pp.Regex("\\\\n").addParseAction(replace_lamba('\\n', '\n'))
    fn_word = pp.Regex("[^ \n]")
    unix_path = pp.Combine(pp.OneOrMore(escaped_space | escaped_newline | fn_word))

    option_short = pp.Word("-") + pp.Word(pp.alphanums)
    option_long = pp.Combine(pp.Word("--") + pp.Word(pp.alphanums) + pp.Optional((pp.Literal(" ") | pp.Literal("=")).addParseAction(replace_lamba(" ", "=")) + sl_string))

    space = pp.OneOrMore(pp.White(" ").suppress())

    wspaces = pp.OneOrMore(pp.White("\n\r\t ").suppress())

    name_stmt = pp.Group(pp.Keyword("name") + string)
    package_stmt = pp.Group(pp.Keyword("package") + pp.OneOrMore(unquoted_sl_string | pp.Literal(" ").suppress()).leaveWhitespace())
    gd_stmt = pp.Group(pp.Keyword("gd") + ((space + option_long) * (0, 3)) + unquoted_sl_string + unix_path)
    run_stmt = pp.Group(pp.Keyword("run") + space + pp.restOfLine)
    install_stmt = pp.Group(pp.Keyword("install") + unix_path + unix_path)
    append_stmt = pp.Group(pp.Keyword("append") + unix_path + wspaces + string)
    put_stmt = pp.Group(pp.Keyword("put") + unix_path + wspaces + string)
    replace_stmt = pp.Group(pp.Keyword("replace") + unix_path + wspaces + string + wspaces + string)
    insert_stmt = pp.Group(pp.Keyword("insert") + unix_path + wspaces + string + wspaces + string)
    rinsert_stmt = pp.Group(pp.Keyword("rinsert") + unix_path + wspaces + string + wspaces + string)
    comment_stmt = pp.Group(pp.Literal("#") + pp.restOfLine)

    """
    Suppresses any statement that is not on it's own line(s).
    """
    stmt = pp.Or([name_stmt,
                  package_stmt,
                  gd_stmt,
                  run_stmt,
                  install_stmt,
                  append_stmt,
                  put_stmt,
                  replace_stmt,
                  insert_stmt,
                  rinsert_stmt,
                  comment_stmt.suppress(),
                  pp.White().suppress()]) + (pp.LineEnd() | ~pp.StringEnd()).suppress()
    calls = pp.OneOrMore(stmt).parseString(recipe)
    return calls


#print("testparse")
#print("{}".format(parse("install\nrun\ninstall")))