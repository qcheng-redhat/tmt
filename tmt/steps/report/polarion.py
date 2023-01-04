import dataclasses
import datetime
import xml.etree.ElementTree as ET
from typing import Optional

from requests import post

import tmt
import tmt.steps
import tmt.steps.report
from tmt.utils import field

from .junit import make_junit_xml

DEFAULT_NAME = 'xunit.xml'


@dataclasses.dataclass
class ReportPolarionData(tmt.steps.report.ReportStepData):
    file: Optional[str] = field(
        default=None,
        option='--file',
        metavar='FILE',
        help='Path to the file to store xUnit in.'
        )

    upload: bool = field(
        default=True,
        option=('--upload / --no-upload'),
        is_flag=True,
        help="Whether to upload results to Polarion."
        )

    project_id: Optional[str] = field(
        default=None,
        option='--project-id',
        metavar='ID',
        help='Use specific Polarion project ID.'
        )

    testrun_title: Optional[str] = field(
        default=None,
        option='--testrun-title',
        metavar='TITLE',
        help='Use specific TestRun title.'
        )


@tmt.steps.provides_method('polarion')
class ReportPolarion(tmt.steps.report.ReportPlugin):
    """
    Write test results into a xUnit file and upload to Polarion
    """

    _data_class = ReportPolarionData

    def go(self) -> None:
        """ Go through executed tests and report into Polarion """
        super().go()

        from tmt.export.polarion import find_polarion_case_ids, import_polarion
        import_polarion()
        from tmt.export.polarion import PolarionWorkItem
        assert PolarionWorkItem

        title = self.get(
            'testrun_title',
            self.step.plan.name.rsplit('/', 1)[1] +
            datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
        title = title.replace('-', '_')
        project_id = self.get('project-id')
        upload = self.get('upload')

        junit_suite = make_junit_xml(self)
        xml_tree = ET.fromstring(junit_suite.to_xml_string([junit_suite]))

        properties = {
            'polarion-project-id': project_id,
            'polarion-user-id': PolarionWorkItem._session.user_id,
            'polarion-testrun-id': title,
            'polarion-project-span-ids': project_id}
        testsuites_properties = ET.SubElement(xml_tree, 'properties')
        for name, value in properties.items():
            ET.SubElement(testsuites_properties, 'property', attrib={
                'name': name, 'value': value})

        testsuite = xml_tree.find('testsuite')
        project_span_ids = xml_tree.find(
            '*property[@name="polarion-project-span-ids"]')

        for result in self.step.plan.execute.results():
            if not result.ids or not any(result.ids.values()):
                raise tmt.utils.ReportError(
                    f"Test Case {result.name} is not exported to Polarion, "
                    "please run 'tmt tests export --how polarion' on it")
            work_item_id, test_project_id = find_polarion_case_ids(result.ids)

            if test_project_id is None:
                raise tmt.utils.ReportError("Test case missing or not found in Polarion")

            assert work_item_id is not None
            assert project_span_ids is not None

            if test_project_id not in project_span_ids.attrib['value']:
                project_span_ids.attrib['value'] += f',{test_project_id}'

            test_properties = {
                'polarion-testcase-id': work_item_id,
                'polarion-testcase-project-id': test_project_id}

            assert testsuite is not None
            test_case = testsuite.find(f"*[@name='{result.name}']")
            assert test_case is not None
            properties_elem = ET.SubElement(test_case, 'properties')
            for name, value in test_properties.items():
                ET.SubElement(properties_elem, 'property', attrib={
                    'name': name, 'value': value})

        assert self.workdir is not None

        f_path = self.get("file", self.workdir / DEFAULT_NAME)
        with open(f_path, 'wb') as fw:
            ET.ElementTree(xml_tree).write(fw)

        if upload:
            server_url = str(PolarionWorkItem._session._server.url)
            polarion_import_url = (
                f'{server_url}{"" if server_url.endswith("/") else "/"}'
                'import/xunit')
            auth = (
                PolarionWorkItem._session.user_id,
                PolarionWorkItem._session.password)

            response = post(
                polarion_import_url, auth=auth,
                files={'file': ('xunit.xml', ET.tostring(xml_tree))})
            self.info(
                f'Response code is {response.status_code} with text: {response.text}')
        else:
            self.info(f"xUnit file saved at: {f_path}")
            self.info("Polarion upload can be done manually using command:")
            self.info(
                "curl -k -u <USER>:<PASSWORD> -X POST -F file=@<XUNIT_XML_FILE_PATH> "
                "<POLARION_URL>/polarion/import/xunit")
