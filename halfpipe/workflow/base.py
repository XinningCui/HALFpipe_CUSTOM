# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from uuid import uuid5
from pathlib import Path

from nipype.pipeline import engine as pe
from nipype.interfaces import freesurfer as fs
from fmriprep import config

from .factory import FactoryContext
from .mriqc import MriqcFactory
from .fmriprep import FmriprepFactory
from .setting import SettingFactory
from .feature import FeatureFactory
from .model import ModelFactory

from .collect import collect_bold_files
from .convert import convert_all
from .memory import MemoryCalculator, patch_mem_gb
from .constants import constants
from ..io.index import Database, BidsDatabase
from ..io.file import cacheobj, uncacheobj
from ..model.spec import loadspec
from ..utils import logger, deepcopyfactory
from .. import __version__


class IdentifiableWorkflow(pe.Workflow):
    def __init__(self, name, base_dir=None, uuid=None):
        super(IdentifiableWorkflow, self).__init__(name, base_dir=base_dir)

        self.uuid = uuid
        self.bids_to_sub_id_map = dict()


def init_workflow(workdir):
    """
    initialize nipype workflow

    :param spec
    """

    spec = loadspec(workdir=workdir)
    assert spec is not None, "A spec file could not be loaded"
    logger.info("Initializing file database")
    database = Database(spec)
    # uuid depends on the spec file, the files found and the version of the program
    uuid = uuid5(spec.uuid, database.sha1 + __version__)

    workflow = uncacheobj(workdir, "workflow", uuid)
    if workflow is not None:
        return workflow

    # init classes that use the database

    bids_database = BidsDatabase(database)
    memcalc = MemoryCalculator(database)

    # create parent workflow

    uuidstr = str(uuid)[:8]
    logger.info(f"Initializing new workflow {uuidstr}")

    workflow = IdentifiableWorkflow(name=constants.workflowdir, base_dir=workdir, uuid=uuid)
    workflow.config["execution"].update(dict(
        create_report=False,
        crashdump_dir=workflow.base_dir,
        crashfile_format="txt",
        hash_method="content",
        poll_sleep_duration=0.5,
        use_relative_paths=False,
        check_version=False,
    ))

    # create factories

    ctx = FactoryContext(workdir, spec, bids_database, workflow, memcalc)
    fmriprep_factory = FmriprepFactory(ctx)
    setting_factory = SettingFactory(ctx, fmriprep_factory)
    feature_factory = FeatureFactory(ctx, setting_factory)
    model_factory = ModelFactory(ctx, feature_factory)

    bold_file_paths_dict = collect_bold_files(
        database, setting_factory, feature_factory
    )

    # write out

    convert_all(database, bids_database, bold_file_paths_dict)

    for bold_file_path in bold_file_paths_dict.keys():
        bids_path = bids_database.tobids(bold_file_path)

        subject = database.tagval(bold_file_path, "sub")
        bids_subject = bids_database.tagval(bids_path, "sub")

        workflow.bids_to_sub_id_map[bids_subject] = subject

    bids_dir = Path(workdir) / "rawdata"
    bids_database.write(bids_dir)

    # setup preprocessing

    if spec.global_settings.get("run_mriqc") is True:
        mriqc_factory = MriqcFactory(ctx)
        mriqc_factory.setup(
            workdir,
            list(bold_file_paths_dict.keys()),
        )

    if spec.global_settings.get("run_fmriprep") is True:
        fmriprep_factory.setup(
            workdir,
            list(bold_file_paths_dict.keys()),
        )

        if spec.global_settings.get("run_halfpipe") is True:
            setting_factory.setup(bold_file_paths_dict)
            feature_factory.setup(bold_file_paths_dict)
            model_factory.setup()

    # patch workflow

    config_factory = deepcopyfactory(workflow.config)
    uses_freesurfer = False

    omp_nthreads = config.nipype.omp_nthreads
    assert isinstance(omp_nthreads, int)
    for node in workflow._get_all_nodes():

        node.config = config_factory()
        if node.name in ["split"]:
            node.config["execution"]["hash_method"] = "content"

        # update memory predictions
        patch_mem_gb(node, omp_nthreads, memcalc)

        if isinstance(node.interface, fs.FSCommand):
            uses_freesurfer = True

        node.overwrite = None
        node.run_without_submitting = False  # run all nodes in multiproc

    logger.info(f"Finished workflow {uuidstr}")
    cacheobj(workdir, "workflow", workflow)

    # check
    if uses_freesurfer:
        from niworkflows.utils.misc import check_valid_fs_license

        if not check_valid_fs_license():
            logger.error(
                "fMRIPrep needs to use FreeSurfer commands, but a valid license file for FreeSurfer could not be found. \n"
                "HALFpipe looked for an existing license file at several paths, in this order: \n"
                '1) a "license.txt" file in your HALFpipe working directory \n'
                '2) command line argument "--fs-license-file" \n'
                "Get it (for free) by registering at https://surfer.nmr.mgh.harvard.edu/registration.html"
            )
            return

    return workflow
