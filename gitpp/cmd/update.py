import hashlib
import logging
import os
import shutil
import sys
from git import Repo

base = os.path.abspath(os.path.join(os.path.dirname(sys.argv[0]), '..', '..'))
sys.path.append(base)

from gitpp import logger
from gitpp import patch
from gitpp.cache import CompositeKVStore
from gitpp.cache import SimpleFSStore
from gitpp.filter.gitfilter import GitRevListFilter
from gitpp.kvbulk import KVBulkNaive
from gitpp.objectstore import ObjectStore
from gitpp.patchfile import GitPatchfileFactory, PatchfileError
from gitpp.scm.gitcontroller import GitController
from gitpp.segment import filter_segments
from gitpp.segmentwalk import SegmentWalk
from gitpp.testingplan import TestingPlan


def segmentid(segment):
    return "%s-%s" % (segment[-1], segment[0])


class BOMConstructionFactory(object):
    def __init__(self, ctl, segmentmap):
        self._ctl = ctl
        self._segmentmap = segmentmap

    def create(self, sids):
        result = {}

        for sid in sids:
            logger.debug('Constructing bom from segment: %s', sid)
            result[sid] = self._ctl.bom_from_segment(self._segmentmap[sid])

        return result


class PatchTestingFactory(object):
    def __init__(self, ctl, plan, patches):
        self._ctl = ctl
        self._plan = plan
        self._patches = patches

    def create(self, patchkeys):
        result = {}

        for patchkey in patchkeys:
            pid = patchkey[0][0]
            p = self._patches[pid]
            commit = self._plan[patchkey]

            logger.debug('Testing patch on %s: %s' % (commit, p.path))

            newp = ctl.testpatch(commit, p.path)

            # Put result into cache for the examined blobids
            if newp == None:
                result[patchkey] = dict((key, None) for key in patchkey)
            else:
                hunks_by_blob = {}
                currentblob = None
                for (sym, data) in patch.parse(newp.splitlines()):
                    if sym == 'i':
                        currentblob = hunks_by_blob.setdefault(data[0], [])
                    elif sym == '@':
                        currentblob.append(data)
                result[patchkey] = dict(((pid, blobid), hunks) for (blobid, hunks) in hunks_by_blob.iteritems())

        return result


if __name__ == '__main__':

    logging.basicConfig()
    logger.setLevel(logging.DEBUG)

    bomcache = SimpleFSStore()
    bomcache.directory = '/tmp/bomcache'
    bomcache.multilevel = False
    bomcache.pfxlen = 0
    bombulk = KVBulkNaive(bomcache)

    patchcache = SimpleFSStore()
    patchcache.directory = '/tmp/patchcache'
    patchcache.multilevel = True
    patchbulk = KVBulkNaive(CompositeKVStore(patchcache))

    repo = Repo('.')
    ctl = GitController(repo)

    patchfilefactory = GitPatchfileFactory(repo)
    patches = {}
    for patchpath in sys.argv[1:]:
        try:
            p = patchfilefactory.fromfile(patchpath)
            patches[p.patchid] = p
        except PatchfileError, why:
            logger.warn('Skipping patch because: %s' % str(why))
    ctl.prepare()

    # FIXME: Setup alternative index and point GIT_INDEX_FILE to it

    f = GitRevListFilter(repo, all=True, after='6 months')
    f.prepare()

    segments = filter_segments(ctl.segmentize(), [f])
    segmentmap = dict((segmentid(segment), segment) for segment in segments)
    bomfactory = BOMConstructionFactory(ctl, segmentmap)

    bomstore = ObjectStore(segmentmap.keys(), bombulk, bomfactory)
    bomstore.load()
    bomstore.dump()

    planner = TestingPlan(ctl, patches, [f])
    plan = planner.construct(bomstore.get_all())
    patchfactory = PatchTestingFactory(ctl, plan, patches)
    patchstore = ObjectStore(plan.keys(), patchbulk, patchfactory)
    patchstore.load()
    patchstore.dump()

    ctl.cleanup()
