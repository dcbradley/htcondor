#!/usr/bin/env pytest

import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

import htcondor2

from ornithology import (
    action,
    Condor,
    ClusterState,
    DaemonLog,
    JobStatus,
)


TEST_CASES_ONE = {
    "CarryOn": (
        '{ [ Command = "CarryOn"; ] }',
        JobStatus.HELD,
        "Carrying on according to guidance...",
    ),
    "StartJob": (
        '{ [ Command = "StartJob"; ] }',
        JobStatus.COMPLETED,
        "Starting job as guided...",
    ),
    "Abort": (
        '{ [ Command = "Abort"; ] }',
        JobStatus.HELD,
        "Aborting job as guided...",
    ),
    "RetryTransfer": (
        '{ [ Command = "RetryTransfer"; ], [ Command = "CarryOn"; ] }',
        JobStatus.HELD,
        "Retrying transfer as guided...",
    ),
    "RunDiagnostic": (
        '{ [ Command = "RunDiagnostic"; Diagnostic = "send_ep_logs"; ], [ Command = "CarryOn"; ] }',
        JobStatus.HELD,
        "Running diagnostic 'send_ep_logs' as guided...",
    ),
}

# We can't reliably get a single starter log for each job with ten slots
# when run under ctest for unknown reasons.  Rather than fight it, let's
# just only run the more-complicated tests for now; the simpler tests
# can be re-enabled if these fail.
TEST_CASES = {
    "CarryOn w/ Extra": (
        '{ [ Command = "CarryOn"; Extraneous = True; ] }',
        JobStatus.HELD,
        "Carrying on according to guidance...",
    ),
    "StartJob w/ Extra": (
        '{ [ Command = "StartJob"; Extraneous = True; ] }',
        JobStatus.COMPLETED,
        "Starting job as guided...",
    ),
    "Abort w/ Extra": (
        '{ [ Command = "Abort"; Extraneous = True; ] }',
        JobStatus.HELD,
        "Aborting job as guided...",
    ),
    "RetryTransfer w/ Extra": (
        '{ [ Command = "RetryTransfer"; Extraneous = True; ], [ Command = "CarryOn"; Extraneous = True; ] }',
        JobStatus.HELD,
        "Retrying transfer as guided...",
    ),
    "RunDiagnostic w/ Extra": (
        '{ [ Command = "RunDiagnostic"; Diagnostic = "send_ep_logs"; Extraneous = True; ], [ Command = "CarryOn"; Extraneous = True; ] }',
        JobStatus.HELD,
        "Running diagnostic 'send_ep_logs' as guided...",
    ),
}


@action
def path_to_shadow_wrapper(test_dir):
    return test_dir / "shadow_wrapper"


@action
def the_condor(test_dir, path_to_shadow_wrapper):
    local_dir = test_dir / "condor"

    with Condor(
        local_dir=local_dir,
        config={
            "SHADOW":   path_to_shadow_wrapper.as_posix(),

            # For simplicity, so that each test job gets its own starter log.
            "NUM_CPUS":                     len(TEST_CASES),
        },
    ) as the_condor:
        SBIN = htcondor2.param["SBIN"]
        path_to_shadow_wrapper.write_text(
            "#!/bin/bash\n"
            f'exec {SBIN}/condor_shadow --use-guidance-in-job-ad "$@"\n'
        )
        path_to_shadow_wrapper.chmod(0o777)

        yield the_condor


@action
def the_job_description(test_dir, path_to_sleep):
    return {
        "executable":               path_to_sleep,
        "transfer_executable":      False,
        "should_transfer_files":    True,
        "universe":                 "vanilla",
        "arguments":                5,
        "log":                      (test_dir / "job.log").as_posix(),
        "starter_debug":            "D_FULLDEBUG",
        "request_cpus":             1,
        "request_memory":           1,
        "transfer_input_files":     "http://no-such.tld/example",
    }


@action
def the_job_handles(the_condor, the_job_description):
    job_handles = {}
    for name, test_case in TEST_CASES.items():
        (the_guidance, the_expected, _) = test_case

        complete_job_description = {
            ** the_job_description,
            "+_condor_guidance_test_case": the_guidance,
        }
        job_handle = the_condor.submit(
            description=complete_job_description,
            count=1,
        )

        job_handles[name] = job_handle

    yield job_handles


@action(params={name: name for name in TEST_CASES})
def the_job_tuple(request, the_job_handles):
    return (request.param, the_job_handles[request.param])


@action
def the_job_name(the_job_tuple):
    return the_job_tuple[0]


@action
def the_job_handle(the_job_tuple):
    return the_job_tuple[1]


@action
def the_expected_state(the_job_name):
    return TEST_CASES[the_job_name][1]


@action
def the_expected_log_line(the_job_name):
    return TEST_CASES[the_job_name][2]


@action
def the_completed_job(the_condor, the_job_handle):
    assert the_job_handle.wait(
        timeout=20,
        condition=ClusterState.all_terminal
    )

    return the_job_handle


@action
def the_starter_log(test_dir, the_completed_job):
    starter_log_path = (test_dir / "condor" / "log" / f"StarterLog.slot1_{the_completed_job.clusterid}")
    starter_log = DaemonLog(starter_log_path)
    return starter_log.open()


class TestGuidanceCommands:

    def test_guidance_command(self,
        the_completed_job, the_expected_state,
        the_starter_log, the_expected_log_line
    ):
        # Did the job carry through to the expected state?
        assert the_completed_job.state.status_exactly( 1, the_expected_state )

        # Did the start actually execute the guidance it was given?
        assert the_starter_log.wait(
            timeout=1,
            condition=lambda line: the_expected_log_line in line.message,
        )
