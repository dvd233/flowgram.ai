/**
 * Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
 * SPDX-License-Identifier: MIT
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { WorkflowJSON, WorkflowNodeJSON } from '@flowgram.ai/free-layout-editor';

import { generateUniqueWorkflow } from './unique-workflow';

const { generateId } = vi.hoisted(() => ({ generateId: vi.fn() }));

vi.mock('nanoid', () => ({ customAlphabet: () => generateId }));

const node = (id: string): WorkflowNodeJSON => ({
  id,
  type: 'test',
  meta: { position: { x: 0, y: 0 } },
  data: { title: id },
});

beforeEach(() => {
  generateId.mockReset();
  generateId.mockImplementation(() => {
    throw new Error('Unexpected ID generation');
  });
});

describe('generateUniqueWorkflow', () => {
  it('retries IDs already assigned to another copied node and preserves edge endpoints', () => {
    generateId
      .mockReturnValueOnce('111111')
      .mockReturnValueOnce('111111')
      .mockReturnValueOnce('222222');
    const json: WorkflowJSON = {
      nodes: [node('a'), node('b')],
      edges: [{ sourceNodeID: 'a', targetNodeID: 'b' }],
    };

    generateUniqueWorkflow({ json, isUniqueId: (id) => !['a', 'b'].includes(id) });

    expect(json.nodes.map((item) => item.id)).toEqual(['111111', '222222']);
    expect(json.nodes.map((item) => item.data?.title)).toEqual(['a', 'b']);
    expect(json.edges).toEqual([{ sourceNodeID: '111111', targetNodeID: '222222' }]);
    expect(generateId).toHaveBeenCalledTimes(3);
  });

  it.each([false, true])(
    'reserves unchanged clipboard IDs before allocation (retained first: %s)',
    (retainedFirst) => {
      generateId.mockReturnValueOnce('111111').mockReturnValueOnce('222222');
      const nodes = retainedFirst ? [node('111111'), node('a')] : [node('a'), node('111111')];
      const json: WorkflowJSON = { nodes, edges: [] };

      generateUniqueWorkflow({ json, isUniqueId: (id) => id !== 'a' });

      expect(json.nodes.map((item) => item.id)).toEqual(
        retainedFirst ? ['111111', '222222'] : ['222222', '111111']
      );
      expect(generateId).toHaveBeenCalledTimes(2);
    }
  );

  it('keeps IDs and the input object unchanged when they are already unique', () => {
    const json: WorkflowJSON = {
      nodes: [node('a'), node('b')],
      edges: [{ sourceNodeID: 'a', targetNodeID: 'b' }],
    };
    const original = structuredClone(json);

    expect(generateUniqueWorkflow({ json, isUniqueId: () => true })).toBe(json);
    expect(json).toEqual(original);
    expect(generateId).not.toHaveBeenCalled();
  });

  it('continues to reject IDs occupied by the destination canvas', () => {
    generateId.mockReturnValueOnce('occupied').mockReturnValueOnce('111111');
    const json: WorkflowJSON = { nodes: [node('a')], edges: [] };

    generateUniqueWorkflow({ json, isUniqueId: (id) => !['a', 'occupied'].includes(id) });

    expect(json.nodes[0].id).toBe('111111');
    expect(generateId).toHaveBeenCalledTimes(2);
  });

  it('uses distinct IDs across nested blocks while preserving their edges and variable references', () => {
    generateId
      .mockReturnValueOnce('111111')
      .mockReturnValueOnce('111111')
      .mockReturnValueOnce('222222')
      .mockReturnValueOnce('222222')
      .mockReturnValueOnce('333333');
    const parent = node('parent');
    parent.blocks = [node('a'), node('b')];
    parent.edges = [{ sourceNodeID: 'a', targetNodeID: 'b' }];
    parent.data = {
      internal: { source: 'block-output', blockID: 'a', name: 'value' },
      external: { source: 'block-output', blockID: 'outside', name: 'value' },
    };
    const json: WorkflowJSON = { nodes: [parent], edges: [] };

    generateUniqueWorkflow({ json, isUniqueId: (id) => !['parent', 'a', 'b'].includes(id) });

    expect(parent.id).toBe('111111');
    expect(parent.blocks.map((item) => item.id)).toEqual(['222222', '333333']);
    expect(parent.edges).toEqual([{ sourceNodeID: '222222', targetNodeID: '333333' }]);
    expect(parent.data.internal.blockID).toBe('222222');
    expect(parent.data.external.blockID).toBe('outside');
    expect(generateId).toHaveBeenCalledTimes(5);
  });
});
