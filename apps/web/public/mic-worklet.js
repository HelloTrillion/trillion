// Runs on the audio thread. It does one thing: hand each block of microphone
// samples to the page, which converts and sends them. Kept free of logic so
// everything that can be wrong is in code that has tests.
class MicCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) this.port.postMessage(channel.slice(0));
    return true;
  }
}

registerProcessor('mic-capture', MicCapture);
