#!/usr/bin/env python3

# Copyright (c) Facebook, Inc. and its affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
General utilities for helping writing ParlAI unit and integration tests.
"""

import os
import unittest
import contextlib
import tempfile
import shutil
import io
import signal
from typing import Tuple, Dict, Any
from parlai.core.opt import Opt
import parlai.utils.logging as logging
from parlai.utils.io import PathManager
from pytest_regressions.data_regression import DataRegressionFixture


try:
    import torch

    TORCH_AVAILABLE = True
    GPU_AVAILABLE = torch.cuda.device_count() > 0
except ImportError:
    TORCH_AVAILABLE = False
    GPU_AVAILABLE = False

try:
    import torchvision  # noqa: F401

    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False

try:
    import git

    git_ = git.Git()
    GIT_AVAILABLE = True
except ImportError:
    git_ = None
    GIT_AVAILABLE = False

try:
    import subword_nmt  # noqa: F401

    BPE_INSTALLED = True
except ImportError:
    BPE_INSTALLED = False

try:
    import fairseq  # noqa: F401

    FAIRSEQ_AVAILABLE = True
except ImportError:
    FAIRSEQ_AVAILABLE = False


try:
    import mephisto  # noqa: F401

    MEPHISTO_AVAILABLE = True
except ImportError:
    MEPHISTO_AVAILABLE = False


try:
    import clearml

    CLEARML__AVAILABLE = True
except ImportError:
    CLEARML__AVAILABLE = False


def is_this_circleci():
    """
    Return if we are currently running in CircleCI.
    """
    return bool(os.environ.get('CIRCLECI'))


def skipUnlessTorch(testfn, reason='pytorch is not installed'):
    """
    Decorate a test to skip if torch is not installed.
    """
    return unittest.skipUnless(TORCH_AVAILABLE, reason)(testfn)


def skipUnlessTorch17(testfn, reason='Test requires pytorch 1.7+'):
    if not TORCH_AVAILABLE:
        skip = True
    else:
        from packaging import version

        skip = version.parse(torch.__version__) < version.parse('1.7.0')
    return unittest.skipIf(skip, reason)(testfn)


def skipIfGPU(testfn, reason='Test is CPU-only'):
    """
    Decorate a test to skip if a GPU is available.

    Useful for disabling hogwild tests.
    """
    return unittest.skipIf(GPU_AVAILABLE, reason)(testfn)


def skipUnlessGPU(testfn, reason='Test requires a GPU'):
    """
    Decorate a test to skip if no GPU is available.
    """
    return unittest.skipUnless(GPU_AVAILABLE, reason)(testfn)


def skipUnlessBPE(testfn, reason='Test requires subword NMT'):
    """
    Decorate a test to skip if BPE is not installed.
    """
    return unittest.skipUnless(BPE_INSTALLED, reason)(testfn)


def skipIfCircleCI(testfn, reason='Test disabled in CircleCI'):
    """
    Decorate a test to skip if running on CircleCI.
    """
    return unittest.skipIf(is_this_circleci(), reason)(testfn)


def skipUnlessVision(testfn, reason='torchvision not installed'):
    """
    Decorate a test to skip unless torchvision is installed.
    """
    return unittest.skipUnless(VISION_AVAILABLE, reason)(testfn)


def skipUnlessFairseq(testfn, reason='fairseq not installed'):
    """
    Decorate a test to skip unless fairseq is installed.
    """
    return unittest.skipUnless(FAIRSEQ_AVAILABLE, reason)(testfn)


def skipUnlessMephisto(testfn, reason='mephisto not installed'):
    """
    Decorate a test to skip unless mephisto is installed.
    """
    return unittest.skipUnless(MEPHISTO_AVAILABLE, reason)(testfn)


def skipUnlessClearML(testfn, reason='clearml not installed'):
    """
    Decorate a test to skip unless clearml is installed.
    """
    return unittest.skipUnless(CLEARML__AVAILABLE, reason)(testfn)


class retry(object):
    """
    Decorator for flaky tests. Test is run up to ntries times, retrying on failure.

    :param ntries:
        the number of tries to attempt
    :param log_retry:
        if True, prints to stdout on retry to avoid being seen as "hanging"

    On the last time, the test will simply fail.

    >>> @retry(ntries=10)
    ... def test_flaky(self):
    ...     import random
    ...     self.assertLess(0.5, random.random())
    """

    def __init__(self, ntries=3, log_retry=False):
        self.ntries = ntries
        self.log_retry = log_retry

    def __call__(self, testfn):
        """
        Call testfn(), possibly multiple times on failureException.
        """
        from functools import wraps

        @wraps(testfn)
        def _wrapper(testself, *args, **kwargs):
            for _ in range(self.ntries - 1):
                try:
                    return testfn(testself, *args, **kwargs)
                except testself.failureException:
                    if self.log_retry:
                        logging.debug("Retrying {}".format(testfn))
            # last time, actually throw any errors there may be
            return testfn(testself, *args, **kwargs)

        return _wrapper


def git_ls_files(root=None, skip_nonexisting=True):
    """
    List all files tracked by git.
    """
    filenames = git_.ls_files(root).split('\n')
    if skip_nonexisting:
        filenames = [fn for fn in filenames if PathManager.exists(fn)]
    return filenames


def git_ls_dirs(root=None):
    """
    List all folders tracked by git.
    """
    dirs = set()
    for fn in git_ls_files(root):
        dirs.add(os.path.dirname(fn))
    return list(dirs)


def git_changed_files(skip_nonexisting=True):
    """
    List all the changed files in the git repository.

    :param bool skip_nonexisting:
        If true, ignore files that don't exist on disk. This is useful for
        disregarding files created in main, but don't exist in HEAD.
    """
    fork_point = git_.merge_base('origin/main', 'HEAD').strip()
    filenames = git_.diff('--name-only', fork_point).split('\n')
    if skip_nonexisting:
        filenames = [fn for fn in filenames if PathManager.exists(fn)]
    return filenames


def git_commit_messages():
    """
    Output each commit message between here and main.
    """
    fork_point = git_.merge_base('origin/main', 'HEAD').strip()
    messages = git_.log(fork_point + '..HEAD')
    return messages


def is_new_task_filename(filename):
    """
    Check if a given filename counts as a new task.

    Used in tests and test triggers, and only here to avoid redundancy.
    """
    return (
        'parlai/tasks' in filename
        and 'README' not in filename
        and 'task_list.py' not in filename
    )


@contextlib.contextmanager
def capture_output():
    """
    Suppress all logging output into a single buffer.

    Use as a context manager.

    >>> with capture_output() as output:
    ...     print('hello')
    >>> output.getvalue()
    'hello'
    """
    sio = io.StringIO()
    with contextlib.redirect_stdout(sio), contextlib.redirect_stderr(sio):
        yield sio


@contextlib.contextmanager
def tempdir():
    """
    Create a temporary directory.

    Use as a context manager so the directory is automatically cleaned up.

    >>> with tempdir() as tmpdir:
    ...    print(tmpdir)  # prints a folder like /tmp/randomname
    """
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@contextlib.contextmanager
def timeout(time: int = 30):
    """
    Raise a timeout if a function does not return in time `time`.

    Use as a context manager, so that the signal class can reset it's alarm for
    `SIGALARM`

    :param int time:
        Time in seconds to wait for timeout. Default is 30 seconds.
    """
    assert time >= 0, 'Time specified in timeout must be nonnegative.'

    def _handler(signum, frame):
        raise TimeoutError

    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(time)

    try:
        yield
    except TimeoutError as e:
        raise e
    finally:
        signal.signal(signal.SIGALRM, signal.SIG_IGN)


def train_model(opt: Opt) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Run through a TrainLoop.

    If model_file is not in opt, then this helper will create a temporary
    directory to store the model, dict, etc.

    :return: (stdout, valid_results, test_results)
    :rtype: (str, dict, dict)
    """
    import parlai.scripts.train_model as tms

    with tempdir() as tmpdir:
        if 'model_file' not in opt:
            opt['model_file'] = os.path.join(tmpdir, 'model')
        if 'dict_file' not in opt:
            opt['dict_file'] = os.path.join(tmpdir, 'model.dict')
        # Parse verification
        valid, test = tms.TrainModel.main(**opt)

    return valid, test


def eval_model(
    opt, skip_valid=False, skip_test=False, valid_datatype='valid', test_datatype='test'
):
    """
    Run through an evaluation loop.

    :param opt:
        Any non-default options you wish to set.
    :param bool skip_valid:
        If true skips the valid evaluation, and the first return value will be None.
    :param bool skip_test:
        If true skips the test evaluation, and the second return value will be None.
    :param str valid_datatype:
        If custom datatype required for valid, e.g. train:evalmode, specify here

    :return: (valid_results, test_results)
    :rtype: (dict, dict)

    If model_file is not in opt, then this helper will create a temporary directory
    to store the model files, and clean up afterwards. You can keep the directory
    by disabling autocleanup
    """
    import parlai.scripts.eval_model as ems

    if opt.get('model_file') and not opt.get('dict_file'):
        opt['dict_file'] = opt['model_file'] + '.dict'

    opt['datatype'] = 'valid' if valid_datatype is None else valid_datatype
    valid = None if skip_valid else ems.EvalModel.main(**opt)
    opt['datatype'] = 'test' if test_datatype is None else test_datatype
    test = None if skip_test else ems.EvalModel.main(**opt)

    return valid, test


def display_data(opt):
    """
    Run through a display data run.

    :return: (stdout_train, stdout_valid, stdout_test)
    :rtype: (str, str, str)
    """
    import parlai.scripts.display_data as dd

    parser = dd.setup_args()
    parser.set_params(**opt)
    popt = parser.parse_args([])

    with capture_output() as train_output:
        popt['datatype'] = 'train:stream'
        dd.display_data(popt)
    with capture_output() as valid_output:
        popt['datatype'] = 'valid:stream'
        dd.display_data(popt)
    with capture_output() as test_output:
        popt['datatype'] = 'test:stream'
        dd.display_data(popt)

    return (train_output.getvalue(), valid_output.getvalue(), test_output.getvalue())


def display_model(opt) -> Tuple[str, str, str]:
    """
    Run display_model.py.

    :return: (stdout_train, stdout_valid, stdout_test)
    """
    import parlai.scripts.display_model as dm

    parser = dm.setup_args()
    parser.set_params(**opt)
    popt = parser.parse_args([])
    with capture_output() as train_output:
        # evalmode so that we don't hit train_step
        popt['datatype'] = 'train:evalmode:stream'
        dm.display_model(popt)
    with capture_output() as valid_output:
        popt['datatype'] = 'valid:stream'
        dm.display_model(popt)
    with capture_output() as test_output:
        popt['datatype'] = 'test:stream'
        dm.display_model(popt)
    return (train_output.getvalue(), valid_output.getvalue(), test_output.getvalue())


class AutoTeacherTest:
    stream = True

    def _safe(self, obj):
        from parlai.core.message import Message

        if isinstance(obj, list):
            return [self._safe(o) for o in obj]
        elif isinstance(obj, Message):
            obj = dict(obj)
            for key in ['label_candidates', 'eval_label_candidates']:
                if key not in obj:
                    continue
                if isinstance(obj[key], set):
                    obj[key] = sorted(list(obj[key]))
                if len(obj[key]) > 20:
                    obj[key] = list(obj[key][:10]) + list(obj[key][-10:])
            if 'image' in obj:
                # convert the image to base64 encoding so we can store it as a string
                import base64
                import PIL

                assert isinstance(obj['image'], PIL.Image.Image)
                resized = obj['image'].resize((16, 16), PIL.Image.NEAREST)
                gray = resized.convert('LA')
                obj['image_hex'] = base64.b64encode(gray.tobytes()).decode('ascii')
                del obj['image']
            return obj
        else:
            return obj

    def _regression(self, data_regression: DataRegressionFixture, datatype: str):
        import random
        from parlai.core.worlds import create_task
        from parlai.core.params import ParlaiParser

        basename = f"{self.task}_{datatype}".replace(":", "_")

        if self.stream:
            datatype = datatype + ':stream'
        if datatype == 'train:stream':
            datatype = datatype + ':ordered'

        random.seed(42)

        opt = ParlaiParser(True, True).parse_kwargs(
            model='fixed_response',
            fixed_response='none',
            task=self.task,
            datatype=datatype,
            batchsize=1,
        )

        world = create_task(opt, [])
        acts = []
        for _ in range(5):
            world.parley()
            act = self._safe(world.get_acts())
            acts.append(act)

        teacher = world.get_task_agent()
        output = {
            'acts': acts,
            'num_episodes': teacher.num_episodes(),
            'num_examples': teacher.num_examples(),
        }
        data_regression.check(output, basename=basename)

    def test_train_stream_ordered(self, data_regression):
        """
        Test --datatype train:stream:ordered.
        """
        return self._regression(data_regression, 'train')

    def test_valid_stream(self, data_regression):
        """
        Test --datatype valid:stream.
        """
        return self._regression(data_regression, 'valid')

    def test_test_stream(self, data_regression):
        """
        Test --datatype test:stream.
        """
        return self._regression(data_regression, 'test')
