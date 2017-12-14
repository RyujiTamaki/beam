#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Tests for side input utilities."""

import logging
import time
import unittest

import mock

from apache_beam.coders import observable
from apache_beam.runners.worker import sideinputs
from apache_beam.options.value_provider import RuntimeValueProvider


def strip_windows(iterator):
  return [wv.value for wv in iterator]


class FakeSource(object):

  def __init__(self, items):
    self.items = items

  def reader(self):
    return FakeSourceReader(self.items)


class FakeSourceReader(observable.ObservableMixin):

  def __init__(self, items):
    super(FakeSourceReader, self).__init__()
    self.items = items
    self.entered = False
    self.exited = False

  def __iter__(self):
    for item in self.items:
      self.notify_observers(item, is_encoded=isinstance(item, bytes))
      yield item

  def __enter__(self):
    self.entered = True
    return self

  def __exit__(self, exception_type, exception_value, traceback):
    self.exited = True

  @property
  def returns_windowed_values(self):
    return False


class PrefetchingSourceIteratorTest(unittest.TestCase):

  def test_single_source_iterator_fn(self):
    sources = [
        FakeSource([0, 1, 2, 3, 4, 5]),
    ]
    iterator_fn = sideinputs.get_iterator_fn_for_sources(
        sources, max_reader_threads=2)
    assert list(strip_windows(iterator_fn())) == range(6)

  def test_bytes_read_behind_experiment(self):
    mock_read_counter = mock.MagicMock()
    source_records = ['a', 'b', 'c', 'd']
    sources = [
        FakeSource(source_records),
    ]
    iterator_fn = sideinputs.get_iterator_fn_for_sources(
        sources, max_reader_threads=3, read_counter=mock_read_counter)
    assert list(strip_windows(iterator_fn())) == source_records
    mock_read_counter.add_bytes_read.assert_not_called()

  def test_bytes_read_are_reported(self):
    RuntimeValueProvider.set_runtime_options(
        {'experiments': 'sideinput_io_metrics,other'})
    mock_read_counter = mock.MagicMock()
    source_records = ['a', 'b', 'c', 'd']
    sources = [
        FakeSource(source_records),
    ]
    iterator_fn = sideinputs.get_iterator_fn_for_sources(
        sources, max_reader_threads=3, read_counter=mock_read_counter)
    assert list(strip_windows(iterator_fn())) == source_records
    mock_read_counter.add_bytes_read.assert_has_calls(
        [mock.call(len(r)) for r in source_records])

    # Remove runtime options from the runtime value provider.
    RuntimeValueProvider.set_runtime_options({})

  def test_multiple_sources_iterator_fn(self):
    sources = [
        FakeSource([0]),
        FakeSource([1, 2, 3, 4, 5]),
        FakeSource([]),
        FakeSource([6, 7, 8, 9, 10]),
    ]
    iterator_fn = sideinputs.get_iterator_fn_for_sources(
        sources, max_reader_threads=3)
    assert sorted(strip_windows(iterator_fn())) == range(11)

  def test_multiple_sources_single_reader_iterator_fn(self):
    sources = [
        FakeSource([0]),
        FakeSource([1, 2, 3, 4, 5]),
        FakeSource([]),
        FakeSource([6, 7, 8, 9, 10]),
    ]
    iterator_fn = sideinputs.get_iterator_fn_for_sources(
        sources, max_reader_threads=1)
    assert list(strip_windows(iterator_fn())) == range(11)

  def test_source_iterator_single_source_exception(self):
    class MyException(Exception):
      pass

    def exception_generator():
      yield 0
      raise MyException('I am an exception!')

    sources = [
        FakeSource(exception_generator()),
    ]
    iterator_fn = sideinputs.get_iterator_fn_for_sources(sources)
    seen = set()
    with self.assertRaises(MyException):
      for value in iterator_fn():
        seen.add(value.value)
    self.assertEqual(sorted(seen), [0])

  def test_source_iterator_fn_exception(self):
    class MyException(Exception):
      pass

    def exception_generator():
      yield 0
      time.sleep(0.1)
      raise MyException('I am an exception!')

    def perpetual_generator(value):
      while True:
        yield value
        time.sleep(0.1)

    sources = [
        FakeSource(perpetual_generator(1)),
        FakeSource(perpetual_generator(2)),
        FakeSource(perpetual_generator(3)),
        FakeSource(perpetual_generator(4)),
        FakeSource(exception_generator()),
    ]
    iterator_fn = sideinputs.get_iterator_fn_for_sources(sources)
    seen = set()
    with self.assertRaises(MyException):
      for value in iterator_fn():
        seen.add(value.value)
    self.assertEqual(sorted(seen), range(5))


class EmulatedCollectionsTest(unittest.TestCase):

  def test_emulated_iterable(self):
    def _iterable_fn():
      for i in range(10):
        yield i
    iterable = sideinputs.EmulatedIterable(_iterable_fn)
    # Check that multiple iterations are supported.
    for _ in range(0, 5):
      for i, j in enumerate(iterable):
        self.assertEqual(i, j)

  def test_large_iterable_values(self):
    # Here, we create a large collection that would be too big for memory-
    # constained test environments, but should be under the memory limit if
    # materialized one at a time.
    def _iterable_fn():
      for i in range(10):
        yield ('%d' % i) * (200 * 1024 * 1024)
    iterable = sideinputs.EmulatedIterable(_iterable_fn)
    # Check that multiple iterations are supported.
    for _ in range(0, 3):
      for i, j in enumerate(iterable):
        self.assertEqual(('%d' % i) * (200 * 1024 * 1024), j)


if __name__ == '__main__':
  logging.getLogger().setLevel(logging.INFO)
  unittest.main()
