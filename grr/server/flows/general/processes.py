#!/usr/bin/env python
"""These are process related flows."""

from grr.lib.rdfvalues import file_finder as rdf_file_finder
from grr.lib.rdfvalues import standard
from grr.lib.rdfvalues import structs as rdf_structs
from grr.proto import flows_pb2
from grr.server import flow
from grr.server import server_stubs
from grr.server.flows.general import file_finder


class ListProcessesArgs(rdf_structs.RDFProtoStruct):
  protobuf = flows_pb2.ListProcessesArgs
  rdf_deps = [
      standard.RegularExpression,
  ]


class ListProcesses(flow.GRRFlow):
  """List running processes on a system."""

  category = "/Processes/"
  behaviours = flow.GRRFlow.behaviours + "BASIC"
  args_type = ListProcessesArgs

  @flow.StateHandler()
  def Start(self):
    """Start processing."""
    self.CallClient(server_stubs.ListProcesses, next_state="IterateProcesses")

  def _FilenameMatch(self, process):
    if not self.args.filename_regex:
      return True
    return self.args.filename_regex.Match(process.exe)

  def _ConnectionStateMatch(self, process):
    if not self.args.connection_states:
      return True

    for connection in process.connections:
      if connection.state in self.args.connection_states:
        return True
    return False

  @flow.StateHandler()
  def IterateProcesses(self, responses):
    """This stores the processes."""

    if not responses.success:
      # Check for error, but continue. Errors are common on client.
      raise flow.FlowError("Error during process listing %s" % responses.status)

    if self.args.fetch_binaries:
      # Filter out processes entries without "exe" attribute and
      # deduplicate the list.
      paths_to_fetch = set()
      for p in responses:
        if p.exe and self.args.filename_regex.Match(
            p.exe) and self._ConnectionStateMatch(p):
          paths_to_fetch.add(p.exe)
      paths_to_fetch = sorted(paths_to_fetch)

      self.Log("Got %d processes, fetching binaries for %d...",
               len(responses), len(paths_to_fetch))

      self.CallFlow(
          file_finder.FileFinder.__name__,
          paths=paths_to_fetch,
          action=rdf_file_finder.FileFinderAction.Download(),
          next_state="HandleDownloadedFiles")

    else:
      # Only send the list of processes if we don't fetch the binaries
      skipped = 0
      for p in responses:
        # It's normal to have lots of sleeping processes with no executable path
        # associated.
        if p.exe:
          if self._FilenameMatch(p) and self._ConnectionStateMatch(p):
            self.SendReply(p)
        else:
          if self.args.connection_states:
            if self._ConnectionStateMatch(p):
              self.SendReply(p)
          else:
            skipped += 1

      if skipped:
        self.Log("Skipped %s entries, missing path for regex" % skipped)

  @flow.StateHandler()
  def HandleDownloadedFiles(self, responses):
    """Handle success/failure of the FileFinder flow."""
    if responses.success:
      for response in responses:
        self.Log("Downloaded %s", response.stat_entry.pathspec)
        self.SendReply(response.stat_entry)

    else:
      self.Log("Download of file %s failed %s", responses.request_data["path"],
               responses.status)

  def NotifyAboutEnd(self):
    if self.runner.IsWritingResults():
      self.Notify("ViewObject", self.urn, "ListProcesses completed.")
