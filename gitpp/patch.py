import re

# Regex for matching a hunk
HUNK_PATTERN = re.compile(r'^@@ -(\d+)(,(\d+))? \+(\d+)(,(\d+)) @@?')

class ParseError(Exception):
    pass


def parse(source):
    """
    Parse patch file and yield one tuple (symbol, content) for each line.
    Symbols is one of:
    'i':    Index line (old blobid, new blobid)
    'a':    Path for old file
    'b':    Path for new file
    '@':    Start new hunk (deletes, inserts)
    '+':    Insert line
    '-':    Remove line
    '=':    Context line
    '?':    Unknown/uninteresting line (git specific)
    """

    inserts = 0
    deletes = 0

    for line in source:
        # Spool rest of hunk
        if deletes > 0 or inserts > 0:
            if line.startswith('-'):
                deletes -= 1
                yield ('-', line[1:])

            elif line.startswith('+'):
                inserts -= 1
                yield ('+', line[1:])

            elif line.startswith(' '):
                inserts -= 1
                deletes -= 1
                yield ('=', line[1:])

            else:
                raise ParseError('Malformed patch: Unprefixed line within hunk.')

        elif line.startswith('--- '):
            yield ('a', line.split("\t")[0][4:].rstrip())

        elif line.startswith('+++ '):
            yield ('b', line.split("\t")[0][4:].rstrip())

        elif line.startswith('@@ -'):
            result = HUNK_PATTERN.match(line)
            if result:
                # Start spooling lines following the hunk header
                if result.group(3) == None:
                    deletes = 1
                else:
                    deletes = int(result.group(3))
                if result.group(6) == None:
                    inserts = 1
                else:
                    inserts = int(result.group(6))

                data = (int(result.group(1)), deletes, int(result.group(4)), inserts)
                yield ('@', data)
            else:
                # FIXME: throw exception: malformed hunk
                yield ('?', line)

        elif line.startswith('index '):
            commits = line[6:].split()[0]
            (old, new) = commits.split('..')
            yield ('i', (old, new))

        else:
            yield ('?', line)

    if deletes > 0 or inserts > 0:
        raise ParseError('Malformed patche: Unexpected end of file')


def pathstrip(path, pfxlen=1):
    if (path == '/dev/null'):
        return None

    # Strip prefix (-p parameter)
    if (pfxlen > 0):
        path = path.split("/", pfxlen)[-1]

    return path
