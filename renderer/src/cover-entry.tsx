import React from 'react';
import {Composition, registerRoot} from 'remotion';
import {CoverStudio} from './CoverStudio';

registerRoot(() => <Composition id="CoverStudio" component={CoverStudio} width={1920} height={1080}
  fps={30} durationInFrames={1} defaultProps={{title: '视频标题', image: 'portrait.png', width: 1920, height: 1080}}
  calculateMetadata={({props}) => ({width: props.width, height: props.height})}/>);
