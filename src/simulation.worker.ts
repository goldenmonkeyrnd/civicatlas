import type { SimulationReply, SimulationRequest } from './outreach';
import { runSimulation } from './simulation';

/** The UI terminates this Worker when a scenario changes and ignores stale IDs. */
const scope = globalThis as unknown as {
  onmessage: ((event: { data: SimulationRequest }) => void) | null;
  postMessage: (reply: SimulationReply) => void;
};
scope.onmessage = (event) => {
  const request = event.data;
  if (request?.type !== 'run' || !Number.isSafeInteger(request.requestId) || request.requestId < 0) {
    scope.postMessage({ type: 'error', requestId: Number.isSafeInteger(request?.requestId) ? request.requestId : -1, message: 'The simulation request is malformed.' });
    return;
  }
  try {
    const result = runSimulation(request);
    scope.postMessage({ type: 'result', requestId: request.requestId, result });
  } catch (error) {
    scope.postMessage({ type: 'error', requestId: request.requestId, message: error instanceof Error ? error.message : 'The simulation could not complete.' });
  }
};
