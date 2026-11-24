/* CiviVoice - thin wrapper around the browser Web Speech API (SpeechRecognition) for English and Kannada.
 *
 * Honest limits (also in the README):
 *  - Recognition runs in the BROWSER. Chrome/Edge send the audio to the vendor's cloud speech service, so it needs
 *    internet and a secure context (https:// or http://localhost). Firefox does not implement SpeechRecognition.
 *  - Whether Kannada (kn-IN) works depends on the browser/OS/device. There is no reliable way to know in advance; we find
 *    out when recognition starts and report it plainly. The typed fallback is always available.
 *  - Text is returned in the language that was spoken. It is NEVER translated here.
 */
(function (global) {
  'use strict';
  var LOCALES = { en: 'en-IN', kn: 'kn-IN' };
  var SR = global.SpeechRecognition || global.webkitSpeechRecognition;

  var MESSAGES = {
    unsupported: 'Voice input is not supported in this browser. Please use Chrome or Edge, or type your complaint below.',
    insecure: 'Voice input needs a secure page (https:// or http://localhost). Please type your complaint below.',
    'not-allowed': 'Microphone access is blocked. Click the lock icon in the address bar, allow the microphone for this site, then press the microphone again.',
    'service-not-allowed': 'The browser refused to start its speech service (microphone blocked or speech service disabled). Check the site permissions, or type your complaint.',
    'audio-capture': 'No microphone was found or it is in use by another app. Connect a microphone, or type your complaint.',
    'no-speech': 'No speech was heard. Press the microphone and speak after it starts listening, or type your complaint.',
    network: 'The speech service could not be reached. Voice recognition needs an internet connection. You can type your complaint instead.',
    'language-not-supported': 'This browser or device cannot recognise the selected language. Please type your complaint, or try Chrome/Edge on a device with that language installed.',
    'kn-empty': 'Nothing was recognised. Kannada voice recognition is not available on every browser or device - please type your complaint, or try again in a quieter place.',
    aborted: 'Listening was cancelled.',
    generic: 'Voice recognition stopped unexpectedly. Please try again or type your complaint.'
  };

  function CiviVoice(opts) {
    this.opts = opts || {};
    this.rec = null;
    this.listening = false;
    this.gotResult = false;
    this.failed = false;
  }
  CiviVoice.prototype.supported = !!SR;
  CiviVoice.locale = function (lang) { return LOCALES[lang] || LOCALES.en; };
  CiviVoice.messages = MESSAGES;

  CiviVoice.prototype._state = function (s, info) { if (this.opts.onState) this.opts.onState(s, info); };
  CiviVoice.prototype._fail = function (code) {
    this.failed = true;
    if (this.opts.onError) this.opts.onError(code, MESSAGES[code] || MESSAGES.generic);
  };

  CiviVoice.prototype.start = function () {
    var self = this;
    if (this.listening) return;
    if (!SR) { this._fail('unsupported'); return; }
    if (global.isSecureContext === false) { this._fail('insecure'); return; }
    var lang = (this.opts.getLang && this.opts.getLang()) || 'en';
    var rec = new SR();
    rec.lang = LOCALES[lang] || LOCALES.en;
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = 1;
    this.rec = rec; this.gotResult = false; this.failed = false;

    rec.onstart = function () { self.listening = true; self._state('listening', { lang: rec.lang }); };
    rec.onresult = function (ev) {
      var interim = '', final = '';
      for (var i = ev.resultIndex; i < ev.results.length; i++) {
        var t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) final += t; else interim += t;
      }
      if (interim && self.opts.onInterim) self.opts.onInterim(interim);
      if (final) { self.gotResult = true; if (self.opts.onFinal) self.opts.onFinal(final.trim(), lang); }
    };
    rec.onerror = function (ev) { self._fail(ev.error || 'generic'); };
    rec.onend = function () {
      self.listening = false;
      if (!self.gotResult && !self.failed) self._fail(lang === 'kn' ? 'kn-empty' : 'no-speech');
      self._state('idle', { gotResult: self.gotResult });
    };
    try { rec.start(); } catch (e) { this._fail('generic'); }
  };

  CiviVoice.prototype.stop = function () { if (this.rec && this.listening) { try { this.rec.stop(); } catch (e) { /* ignore */ } } };
  CiviVoice.prototype.cancel = function () { if (this.rec) { this.failed = true; try { this.rec.abort(); } catch (e) { /* ignore */ } } };

  global.CiviVoice = CiviVoice;
})(window);
