# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from pathlib import Path

from nipype.pipeline import engine as pe

from fmriprep import config
from fmriprep.cli.workflow import build_workflow

from .factory import Factory
from .report import init_anat_report_wf, init_func_report_wf

from ..utils import formatlikebids, deepcopyfactory


def _follow_to_datasink(hierarchy, node, attr):
    wf = hierarchy[-1]
    for _, target, data in wf._graph.out_edges([node], data=True):
        connect = data.get("connect", dict())
        for inattr, outattr in connect:
            if inattr == attr:
                if isinstance(target, pe.Workflow):
                    nodename, attrname = outattr.split(".")
                    res = _follow_to_datasink(
                        [*hierarchy, target], target.get_node(nodename), attrname
                    )  # recursion
                    if res is not None:
                        _, resnode, _ = res
                        if resnode.name.startswith("ds_"):
                            return res
                if target.name.startswith("ds_") and outattr == "in_file":
                    return hierarchy, target, "out_file"
    return hierarchy, node, attr


def _find_child(hierarchy, name):
    wf = hierarchy[-1]
    for node in wf._graph.nodes():
        if node.name == name:
            return hierarchy, node
        elif isinstance(node, pe.Workflow):
            res = _find_child([*hierarchy, node], name)
            if res is not None:
                return res


class FmriprepFactory(Factory):
    def __init__(self, ctx):
        super(FmriprepFactory, self).__init__(ctx)

    def setup(self, workdir, boldfilepaths):
        spec = self.spec
        database = self.database
        bidsdatabase = self.bidsdatabase
        workflow = self.workflow
        uuidstr = str(workflow.uuid)[:8]

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

        # init fmriprep config
        output_dir = Path(workdir) / "derivatives"
        output_dir.mkdir(parents=True, exist_ok=True)

        subjects = [*database.tagvalset("sub", filepaths=boldfilepaths)]
        bidssubjects = map(formatlikebids, subjects)

        ignore = ["sbref"]
        if spec.global_settings.get("slice_timing") is not True:
            ignore.append("slicetiming")

        skull_strip_t1w = spec.global_settings.get("skull_strip_algorithm") in ["ants", "auto"]

        config.from_dict(
            {
                "bids_dir": bids_dir,
                "output_dir": output_dir,
                "log_dir": workdir,
                "participant_label": bidssubjects,
                "ignore": ignore,
                "use_aroma": False,
                "skull_strip_t1w": skull_strip_t1w,
                "anat_only": spec.global_settings.get("anat_only"),
                "write_graph": spec.global_settings.get("write_graph"),
                "hires": spec.global_settings.get("hires"),
                "run_reconall": spec.global_settings.get("run_reconall"),
                "t2s_coreg": spec.global_settings.get("t2s_coreg"),
                "medial_surface_nan": spec.global_settings.get("medial_surface_nan"),
                "output_spaces": spec.global_settings.get("output_spaces"),
                "bold2t1w_dof": spec.global_settings.get("bold2t1w_dof"),
                "fmap_bspline": spec.global_settings.get("fmap_bspline"),
                "force_syn": spec.global_settings.get("force_syn"),
                "longitudinal": spec.global_settings.get("longitudinal"),
                "regressors_all_comps": spec.global_settings.get("regressors_all_comps"),
                "regressors_dvars_th": spec.global_settings.get("regressors_dvars_th"),
                "regressors_fd_th": spec.global_settings.get("regressors_fd_th"),
                "skull_strip_fixed_seed": spec.global_settings.get("skull_strip_fixed_seed"),
                "skull_strip_template": spec.global_settings.get("skull_strip_template"),
                "aroma_err_on_warn": spec.global_settings.get("aroma_err_on_warn"),
                "aroma_melodic_dim": spec.global_settings.get("aroma_melodic_dim"),
            }
        )
        nipype_dir = Path(workdir) / "nipype"
        nipype_dir.mkdir(parents=True, exist_ok=True)
        config_file = nipype_dir / f"fmriprep.config.{uuidstr}.toml"
        config.to_filename(config_file)

        retval = dict()
        build_workflow(config_file, retval)
        fmriprep_wf = retval["workflow"]
        workflow.add_nodes([fmriprep_wf])

        anat_report_wf_factory = deepcopyfactory(init_anat_report_wf(workdir=self.workdir, memcalc=self.memcalc))
        for subject_id in subjects:
            hierarchy = self._get_hierarchy("reports_wf", subject_id=subject_id)

            wf = anat_report_wf_factory()
            hierarchy[-1].add_nodes([wf])
            hierarchy.append(wf)

            inputnode = wf.get_node("inputnode")
            inputnode.inputs.tags = {
                "sub": subject_id
            }

            self.connect(hierarchy, inputnode, subject_id=subject_id)

        func_report_wf_factory = deepcopyfactory(init_func_report_wf(workdir=self.workdir, memcalc=self.memcalc))
        for boldfilepath in boldfilepaths:
            hierarchy = self._get_hierarchy("reports_wf", sourcefile=boldfilepath)

            wf = func_report_wf_factory()
            hierarchy[-1].add_nodes([wf])
            hierarchy.append(wf)

            inputnode = wf.get_node("inputnode")
            inputnode.inputs.tags = database.tags(boldfilepath)

            self.connect(hierarchy, inputnode, subject_id=subject_id)

    def connect(self, nodehierarchy, node, sourcefile=None, subject_id=None, **kwargs):
        """
        connect equally names attrs
        preferentially use datasinked outputs
        """
        connected_attrs = set()

        def _connect(hierarchy):
            workflow = hierarchy[0]
            wf = hierarchy[-1]

            inputattrs = set(node.inputs.copyable_trait_names())
            dsattrs = set(attr for attr in inputattrs if attr.startswith("ds_"))

            outputnode = wf.get_node("outputnode")
            outputattrs = set(outputnode.outputs.copyable_trait_names())
            attrs = (inputattrs & outputattrs) - connected_attrs  # find common attr names
            for attr in attrs:
                outputendpoint = self._endpoint(
                    *_follow_to_datasink(hierarchy, outputnode, attr)
                )
                inputendpoint = self._endpoint(nodehierarchy, node, attr)
                workflow.connect(*outputendpoint, *inputendpoint)
                connected_attrs.add(attr)

            while len(dsattrs) > 0:
                attr = dsattrs.pop()
                childtpl = _find_child(hierarchy, attr)
                if childtpl is not None:
                    outputendpoint = self._endpoint(*childtpl, "out_file")
                    inputendpoint = self._endpoint(nodehierarchy, node, attr)
                    workflow.connect(*outputendpoint, *inputendpoint)
                    connected_attrs.add(attr)

        hierarchy = self._get_hierarchy("fmriprep_wf", sourcefile=sourcefile, subject_id=subject_id)

        wf = hierarchy[-1]

        # anat only
        anat_wf = wf.get_node("anat_preproc_wf")
        if anat_wf is not None:
            _connect([*hierarchy, anat_wf])
            return

        # func and anat
        _connect(hierarchy)

        for name in ["bold_bold_trans_wf", "bold_hmc_wf", "bold_reference_wf", "bold_reg_wf", "bold_sdc_wf"]:
            bold_wf = wf.get_node(name)
            if bold_wf is not None:
                _connect([*hierarchy, bold_wf])

        while wf.get_node("anat_preproc_wf") is None:
            hierarchy.pop()
            wf = hierarchy[-1]
        wf = wf.get_node("anat_preproc_wf")
        _connect([*hierarchy, wf])
