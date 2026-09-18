'''
date:2026.1.10
version: v0.9
'''
from character import *
import time,os

def GetGain(cc):
    mc = input("输入增益对象（出击序号和增益项目之间使用空格分割，角色之间使用逗号分割）1代表atk+2,2代表hp+4").split(",")
    for i in mc:
        pl = i.split(" ")
        if pl[1] == "1":
            cc[int(pl[0]) - 1][1] += 2
        elif pl[1] == "2":
            cc[int(pl[0]) - 1][2] += 4

#这里是一个游戏类似于成品的模拟过程
def RunGame():
    print(P1.GetCharacters())
    #P1.MakeCharacter(cc1), P2.MakeCharacter(cc2)
    cci1=input("输入玩家1的选择").split(" ")
    cc1=P1.MakeCharacter(cci1)
    GetGain(cc1)
    os.system("cls")
    print(P2.GetCharacters())
    cci2=input("输入玩家2的选择").split(" ")
    cc2=P2.MakeCharacter(cci2)
    GetGain(cc2)
    os.system("cls")
    PL = [cc1,cc2]
    BTF = Battlefield()
    print(BTF.SoldierInit(PL))
    print(PL)
    ProcessStr,winnero=BTF.StartBattle()
    print(ProcessStr)
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