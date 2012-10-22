import hashlib
import logging
import os
import pprint
import shutil
import sys
from datetime import datetime
from git import Repo

base = os.path.abspath(os.path.join(os.path.dirname(sys.argv[0]), '..', '..'))
sys.path.append(base)

from gitpp import logger
from gitpp import patch
from gitpp.bomwalk import BOMWalk
from gitpp.cache import CompositeKVStore
from gitpp.cache import SimpleFSStore
from gitpp.filter.gitfilter import GitRevListFilter
from gitpp.kvbulk import KVBulkNaive
from gitpp.objectstore import ObjectStore
from gitpp.scm.gitcontroller import GitController
from gitpp.segment import filter_segments
from gitpp.segmentwalk import SegmentWalk


def patchid(repo, path):
    istream = open(path, 'r')
    out = repo.git.patch_id(istream=istream)
    istream.close()

    return out.split()[0]


def segmentid(segment):
    return "%s-%s" % (segment[-1], segment[0])

class BOMFactory(object):
    def __init__(self, ctl, segmentmap):
        self._ctl = ctl
        self._segmentmap = segmentmap

    def create(self, sids):
        result = {}

        for sid in sids:
            logger.debug('Constructing bom from segment: %s', sid)
            result[sid] = self._ctl.bom_from_segment(self._segmentmap[sid])

        return result

def hunksconstruct(commit, patchkey, patch_path, ctl):
    newp = ctl.testpatch(commit, patch_path)

    # Put result into cache for the examined blobids
    if newp == None:
        result = dict((key, None) for key in patchkey)
    else:
        hunks_by_blob = {}
        currentblob = None
        for (sym, data) in patch.parse(newp.splitlines()):
            if sym == 'i':
                currentblob = hunks_by_blob.setdefault(data[0], [])
            elif sym == '@':
                currentblob.append(data)
        result = dict(((pid, blobid), hunks) for (blobid, hunks) in hunks_by_blob.iteritems())

    return result

if __name__ == '__main__':

    logging.basicConfig()
    logger.setLevel(logging.DEBUG)

    bomcache = SimpleFSStore()
    bomcache.directory = '/tmp/bomcache'
    bomcache.multilevel = False
    bomcache.pfxlen = 0
    bombulk = KVBulkNaive(bomcache)

    patchstore = SimpleFSStore()
    patchstore.directory = '/tmp/patchcache'
    patchstore.multilevel = True

    patchcache = CompositeKVStore(patchstore)

    repo = Repo('.')
    ctl = GitController(repo)

    patches = {}
    for patchpath in sys.argv[1:]:
        try:
            pid = patchid(repo, patchpath)
        except Exception as e:
            logger.warn('Unable to generate patchid, skipping %s' % patchpath)
            continue

        try:
            mtime = datetime.fromtimestamp(os.stat(patchpath).st_mtime)
        except Exception as e:
            logger.warn('Unable to determine mtime, skipping %s' % patchpath)
            continue

        p = {
            'path': patchpath,
            'patchid': pid,
            'mtime': mtime,
            'affected': set()
        }

        try:
            for (sym, data) in patch.parse(open(patchpath, 'r')):
                if (sym == 'a'):
                    p['affected'].add(patch.pathstrip(data))
        except patch.ParseError as e:
            logger.warn('Unable to parse patch, skipping %s' % patchpath)
            continue

        patches[pid] = p

    ctl.prepare()

    # FIXME: Setup alternative index and point GIT_INDEX_FILE to it

    f = GitRevListFilter(repo, all=True, after='6 months')
    f.prepare()

    segments = filter_segments(ctl.segmentize(), [f])
    segmentmap = dict((segmentid(segment), segment) for segment in segments)
    bomfactory = BOMFactory(ctl, segmentmap)

    bomstore = ObjectStore(segmentmap.keys(), bombulk, bomfactory)
    bomstore.load()
    bomstore.dump()

    commit_patch_results = []
    for (sid, bom) in bomstore.get_all().iteritems():
        blobs_by_patch = {}
        walk = BOMWalk(bom)

        for (pid, p) in patches.iteritems():
            blobs_by_patch[pid] = {}
            walk.watch(pid, p['affected'])

        for (commit, pid, paths) in walk.walk():
            if not f.filter(commit):
                continue

            blobs_by_patch[pid].update(paths)

            result = {}

            # Try to load result from cache
            patchkey = [(pid, blobid) for blobid in blobs_by_patch[pid].itervalues()]
            if patchkey in patchcache:
                logger.info('Read result from cache for patch on %s: %s' % (commit, patches[pid]['path']))
                result = patchcache[patchkey]
            else:
                logger.info('Testing patch on %s: %s' % (commit, patches[pid]['path']))
                patchcache[patchkey] = hunksconstruct(commit, patchkey, patches[pid]['path'], ctl)

            commit_patch_results.append((commit, p, result))

    print len(commit_patch_results)
    ctl.cleanup()
