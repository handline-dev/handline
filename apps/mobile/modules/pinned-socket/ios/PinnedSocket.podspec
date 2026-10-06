Pod::Spec.new do |s|
  s.name = 'PinnedSocket'
  s.version = '0.0.1'
  s.summary = 'spike'
  s.homepage = 'https://example.invalid'
  s.license = 'MIT'
  s.author = 'x'
  s.platforms = { :ios => '15.1' }
  s.source = { :git => '' }
  s.static_framework = true
  s.dependency 'ExpoModulesCore'
  s.source_files = '**/*.swift'
end
