'''
date:2026.1.10
version: v0.9
'''
from character import *
import time,os

#这里是一个游戏类似于成品的模拟过程
def RunGame():
    PL = [P1.MakeCharacters(), P2.MakeCharacters()]
    BTF = Battlefield()
    BTF.SoldierInit(PL)
    print(PL)
    winnero=BTF.StartBattle()
    print("胜者" + str(winnero + 1))
    return winnero
    # 写到这里其实init已经完成了，就是能把角色按照要求放进去战场，接下来就是进行战场的判定

WinnerRank=[0,0]
def main():
    global P1
    P1= Player()
    global P2
    P2 = Player()
    for i in range(3):
        if WinnerRank[1]==2 or WinnerRank[0]==2:
            break
        w=RunGame()
        WinnerRank[w]+=1
    if WinnerRank[0]==2:
        print("1最终胜利")
    else:
        print("2最终胜利")

if __name__=='__main__':
    main()