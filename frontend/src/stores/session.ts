import { defineStore } from 'pinia'

export type TeamRole = 'write' | 'readonly'

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '值班管理员',
    shiftLabel: '白班 08:00-20:00',
    scope: '智能爬虫采集平台',
    // 归属受控批次模块的当前团队身份：团队编号 + 读写/只读角色。
    teamId: 'TEAM-A' as string,
    teamName: '采集团队甲' as string,
    role: 'write' as TeamRole,
  }),
  getters: {
    canOperate: (state) => state.operator.length > 0,
    isReadonly: (state) => state.role === 'readonly',
    identityHeaders: (state) => ({
      'Content-Type': 'application/json',
      'X-Team-Id': state.teamId,
      'X-Role': state.role,
    }),
  },
  actions: {
    setShift(label: string) {
      this.shiftLabel = label
    },
    setTeam(teamId: string, teamName: string) {
      this.teamId = teamId
      this.teamName = teamName
    },
    setRole(role: TeamRole) {
      this.role = role
    },
  },
})
