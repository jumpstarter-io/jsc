# jsc

Jumpstarter Console - A command line tool for managing your Jumpstarter projects.

This tool is tested to work on OS X and Linux with python 2.7 and python 3.4.

This README does not provide documentation, for thorough documentation please go to [our wiki](https://github.com/jumpstarter-io/help/wiki).

# Installation
From pypi:

    # pip install jsc

from repo:

    git clone https://github.com/jumpstarter-io/jsc.git
    cd jsc
    python setup.py build; sudo python setup.py install

# Run
See jsc -h for list of arguments.
We recommend that you add your ssh public key on Jumpstarter.
    
    $ jsc $ssh_username
    
To show available commands:

    prompt> help

# Philosophy
We strongly believe that all system setups should be automated. This reduces
risk when setting up new versions but most importantly gives you a definition of
what the system is.

# Getting started
`jsc` gives you tools to backup, revert and deploy both the code and the state in
your assembly.

If you want to try installing wordpress with a test recipe you first have to
remove the current application code:

    clean

Then install it from a recipe:

    deploy https://github.com/jumpstarter-io/wordpress-recipe.git

Installation is now complete, you can test it by starting the application:

    run

# Software version tracking
An important feature in `jsc` is that when your recipe uses the `package` and
`gd` it tracks the versions installed. That way we can contact you when there
are updates available, relieving you of the stress to keep yourself up to date
with all your dependencies.


# Other commands

command | description
--------|------------
backup [ls] | list current backups
backup new | create new backup of /app/code and recipe
backup rm $id | delete a backup
backup du | prints disk usage of all backups
clean | remove "everything" in /app/code
clean --code | same as clean
clean --state | remove everything in /app/state
clean -all | both --state and --code
deploy $local-file | sync and run recipe file
deploy $local-dir | sync directory and run recipe called Jumpstart-Recipe in dir
deploy $git-url | checkout and run Jumpstart-Recipe in root of repo
deploy $github-repo-id | checkout and run Jumpstart-Recipe in root of repo
deploy --dev | deploys in "development mode", gd command will keep repositories usable with git
env | print env.json
revert $id | revert to backup $id
run | run /app/code/init
ssh | open a terminal with ssh to the assembly
sync | synchronize the software version lists with Jumpstarter
status | print assembly information
status -v | print information and software versions
exit | log out of `jsc`


# Recipe language
Recipes are implemented in a very simple imperative language without any control
statements at all. Execution start with the first line and proceeds to the end,
if a command fails, execution is stopped and the failed state is preserved so 
that the developer can examine what has gone wrong.

Working directory is the directory that the recipe is uploaded to.

command | description
--------|------------
name $name | names the recipe for later presentation and refernce
package $pkg.. | installs multiple packages available in the jumpstart package manager
gd [--pkey $pkey] [--branch $branch] [--depth $n] $repo $dst | version tracked git checkout, can use a supplied ssh key to access the repository. The specified branch can also be a tag, but then we can notify on new versions. Depth is only considered when deploy --dev is used
run $cmd $args.. | run arbitrary process in $PATH
install $src $dst | copy file 
append $file $text | append $text to $file
put $file $text | create/replace $file with $text
replace $file $find $replace | find all occurances of $find and replaces with $replace in $file
insert $file $find $insert | like replace but inserts after occurance instead of replacing
rinsert $file $find $insert | like insert but searches from end

