# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from uuid import uuid5
import logging
from pathlib import Path

from nipype.pipeline import engine as pe

from .factory import FactoryContext
from .mriqc import MriqcFactory
from .fmriprep import FmriprepFactory
from .setting import SettingFactory
from .feature import FeatureFactory
from .model import ModelFactory

from .execgraph import init_execgraph
from .memory import MemoryCalculator
from .constants import constants
from ..io import Database, BidsDatabase, cacheobj, uncacheobj
from ..model import loadspec
from ..utils import deepcopyfactory


def init_workflow(workdir, **kwargs):
    """
    initialize nipype workflow

    :param spec
    """

    logger = logging.getLogger("halfpipe")

    spec = loadspec(workdir=workdir)
    assert spec is not None, "A spec file could not be loaded"
    database = Database(spec)
    uuid = uuid5(spec.uuid, database.sha1)

    workflow = uncacheobj(workdir, "workflow", uuid)
    if workflow is not None:
        return init_execgraph(workdir, workflow, **kwargs)

    # create parent workflow
    workflow = pe.Workflow(name=constants.workflowdir, base_dir=workdir)
    workflow.uuid = uuid
    uuidstr = str(uuid)[:8]
    logger.info(f"Initializing new workflow: {uuidstr}")
    workflow.config["execution"].update(
        {
            "crashdump_dir": workflow.base_dir,
            "crashfile_format": "txt",
            "poll_sleep_duration": 0.75,
            "use_relative_paths": False,
            "check_version": False,
        }
    )

    # create factories
    bidsdatabase = BidsDatabase(database)
    memcalc = MemoryCalculator(database)
    ctx = FactoryContext(workdir, spec, bidsdatabase, workflow, memcalc)
    fmriprep_factory = FmriprepFactory(ctx)
    setting_factory = SettingFactory(ctx, fmriprep_factory)
    feature_factory = FeatureFactory(ctx, setting_factory)
    model_factory = ModelFactory(ctx, feature_factory)

    # create bids
    boldfilepaths = setting_factory.sourcefiles | feature_factory.sourcefiles
    for boldfilepath in boldfilepaths:
        t1ws = database.associations(boldfilepath, datatype="anat")
        if t1ws is None:
            continue
        bidsdatabase.put(boldfilepath)
        for filepath in t1ws:
            bidsdatabase.put(filepath)
        fmaps = database.associations(boldfilepath, datatype="fmap")
        if fmaps is None:
            continue
        for filepath in fmaps:
            bidsdatabase.put(filepath)

    bids_dir = Path(workdir) / "rawdata"
    bidsdatabase.write(bids_dir)

    # setup preprocessing
    if spec.global_settings.get("run_mriqc") is True:
        mriqc_factory = MriqcFactory(ctx)
        mriqc_factory.setup(workdir, boldfilepaths)
    if spec.global_settings.get("run_fmriprep") is True:
        fmriprep_factory.setup(workdir, boldfilepaths)

        if spec.global_settings.get("run_halfpipe") is True:
            setting_factory.setup()
            feature_factory.setup()
            model_factory.setup()

    config_factory = deepcopyfactory(workflow.config)
    for node in workflow._get_all_nodes():
        node.config = config_factory()

    logger.info(f"Finished workflow: {uuidstr}")

    cacheobj(workdir, "workflow", workflow)
    return init_execgraph(workdir, workflow, **kwargs)
