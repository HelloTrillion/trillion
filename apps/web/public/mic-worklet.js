// Runs on the audio thread. It does one thing: hand each block of microphone
// samples to the page, every channel of it, which picks one and converts it. Kept
// free of logic so everything that can be wrong is in code that has tests.
class MicCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channels = inputs[0];
    if (channels && channels.length && channels[0].length) {
      this.port.postMessage(channels.map((c) => c.slice(0)));
    }
    return true;
  }
}

registerProcessor('mic-capture', MicCapture);
